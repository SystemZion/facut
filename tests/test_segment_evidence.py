from pathlib import Path
import hashlib
import json
import pytest
from typer.testing import CliRunner
from facut.cli.main import app
from facut.core.models import MediaAsset, MediaKind
from facut.core.project_manager import ProjectManager
from facut.media.probe import probe_media
from facut.media.tools import find_executable, run_tool
from facut.media.segment_evidence import create_segment_evidence

@pytest.fixture
def setup(tmp_path):
    manager = ProjectManager.create(tmp_path / 'project')
    for name, audio in [('original', True), ('silent', False)]:
        path = tmp_path / (name + '.mp4')
        args = [find_executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=s=160x120:r=25:d=3']
        if audio:
            args += ['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=3']
        args += ['-c:v','libx264','-threads','1','-pix_fmt','yuv420p']
        if audio: args += ['-c:a','aac']
        run_tool(args + [path])
        manager.document.media.append(MediaAsset(id=name, kind=MediaKind.VIDEO,
            path=str(path), original_name=path.name, size=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), technical=probe_media(path)))
    # Deliberately link a silent proxy: evidence must still include ORIGINAL sound.
    manager.document.media[0].proxy_path = str(tmp_path/'silent.mp4')
    manager.save()
    return manager


def test_original_audio_and_manifest(setup, tmp_path):
    before = setup.project_file.read_bytes()
    data = create_segment_evidence(setup,'original',start=.5,end=2,output=tmp_path/'out')
    assert data['audio']['status'] == 'included'
    assert data['source']['path'].endswith('original.mp4')
    assert data['range']['duration'] == 1.5
    assert abs(data['output']['technical']['duration']-1.5) < .1
    assert json.loads(Path(data['output']['manifest']).read_text()) == data
    assert setup.project_file.read_bytes() == before
    source = Path(data['source']['path'])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == data['source']['registered_sha256']
    # AAC contains non-silent samples, rather than silently synthesized padding.
    r = run_tool([find_executable('ffmpeg'),'-i',data['output']['video'],'-af','volumedetect','-vn','-f','null','-'])
    assert 'mean_volume: -inf' not in r.stderr
    assert 'mean_volume:' in r.stderr


def test_no_audio(setup):
    data = create_segment_evidence(setup,'silent',start=0,end=1)
    assert data['audio']['status'] == 'absent_in_source'
    assert data['output']['technical']['audio_codec'] is None

@pytest.mark.parametrize('start,end', [(-1,1),(1,1),(2,1),(float('nan'),1),(0,float('inf')),(0,4),(0,61),(0,.001)])
def test_invalid_ranges(setup,tmp_path,start,end):
    output=tmp_path/'invalid'
    with pytest.raises(ValueError):
        create_segment_evidence(setup,'original',start=start,end=end,output=output)
    assert not output.exists()


def test_unknown_and_source_protection(setup,tmp_path):
    with pytest.raises(ValueError): create_segment_evidence(setup,'missing',start=0,end=1)
    original=Path(setup.document.media[0].path)
    before=original.read_bytes()
    with pytest.raises(FileExistsError):
        create_segment_evidence(setup,'original',start=0,end=1,output=original)
    assert original.read_bytes()==before
    output=tmp_path/'existing';output.mkdir();(output/'user.txt').write_text('keep')
    with pytest.raises(FileExistsError):
        create_segment_evidence(setup,'original',start=0,end=1,output=output)
    assert (output/'user.txt').read_text()=='keep'


def test_missing_original_never_falls_back(setup):
    Path(setup.document.media[0].path).unlink()
    with pytest.raises(FileNotFoundError): create_segment_evidence(setup,'original',start=0,end=1)


def test_failure_cleanup(setup,tmp_path,monkeypatch):
    def fail(*args,**kwargs): raise RuntimeError('encoder failed')
    monkeypatch.setattr('facut.media.segment_evidence.run_tool',fail)
    with pytest.raises(RuntimeError):
        create_segment_evidence(setup,'original',start=0,end=1,output=tmp_path/'failed')
    assert not (tmp_path/'failed').exists()


def test_cli_json(setup,tmp_path):
    runner=CliRunner()
    result=runner.invoke(app,['--json','-p',str(setup.project_dir),'proxy','evidence','original','--start','0.5','--end','1.5','-o',str(tmp_path/'cli')])
    assert result.exit_code == 0, result.output
    body=json.loads(result.stdout)
    assert body['data']['audio']['status']=='included'
    bad=runner.invoke(app,['--json','-p',str(setup.project_dir),'proxy','evidence','missing','--start','0','--end','1'])
    assert bad.exit_code != 0
    assert json.loads(bad.stdout)['status'] == 'error'


def test_timings_opt_in(setup):
    data=create_segment_evidence(setup,'original',start=2,end=3,timings=True)
    stages={s['name']:s for s in data['timings']['stages']}
    assert stages['proxy_encode']['status']=='completed'
    assert stages['manifest_write']['wall_seconds']>=0
    assert stages['audio_extract']['wall_seconds'] is None
    assert 'timings' not in json.loads(Path(data['output']['manifest']).read_text())


def test_no_timings_by_default(setup):
    assert 'timings' not in create_segment_evidence(setup,'silent',start=0,end=1)


def test_source_changed_during_encode(setup,tmp_path,monkeypatch):
    import os
    from facut.media import segment_evidence as se
    real=se.run_tool
    def changing(*args,**kwargs):
        value=real(*args,**kwargs)
        source=Path(setup.document.media[0].path)
        stat=source.stat()
        os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000_000))
        return value
    monkeypatch.setattr(se,'run_tool',changing)
    with pytest.raises(ValueError,match='changed'):
        create_segment_evidence(setup,'original',start=0,end=1,output=tmp_path/'changed')
    assert not (tmp_path/'changed').exists()


def test_trailing_audio_not_video_coverage(setup,tmp_path):
    path=Path(setup.document.media[0].path)
    run_tool([find_executable('ffmpeg'),'-y','-v','error','-f','lavfi','-i',
              'testsrc2=s=160x120:r=25:d=1','-f','lavfi','-i',
              'sine=frequency=440:duration=3','-c:v','libx264','-threads','1','-c:a','aac',path])
    with pytest.raises(ValueError,match='exceeds'):
        create_segment_evidence(setup,'original',start=.5,end=2)


def test_odd_height(setup):
    path=Path(setup.document.media[1].path)
    run_tool([find_executable('ffmpeg'),'-y','-v','error','-f','lavfi','-i',
              'testsrc=s=161x121:r=25:d=2','-c:v','libx264','-threads','1','-pix_fmt','yuv444p',path])
    data=create_segment_evidence(setup,'silent',start=0,end=1)
    assert data['output']['technical']['height'] % 2 == 0

@pytest.mark.parametrize('start,end',[(.2,.24),(.24,.28),(.04,.08)])
def test_single_frame_float_boundary(setup,start,end):
    assert create_segment_evidence(setup,'silent',start=start,end=end)['output']['technical']['video_codec']

@pytest.mark.parametrize('fail',[True,False])
def test_competing_public_files_retained(setup,tmp_path,monkeypatch,fail):
    from facut.media import segment_evidence as se
    real=se.run_tool
    bundle=tmp_path/'competing'
    def competing(*args,**kwargs):
        bundle.mkdir()
        (bundle/'clip.mp4').write_bytes(b'foreign video')
        (bundle/'manifest.json').write_bytes(b'foreign manifest')
        if fail: raise RuntimeError('encoder failure after competing writer')
        return real(*args,**kwargs)
    monkeypatch.setattr(se,'run_tool',competing)
    with pytest.raises((RuntimeError,FileExistsError)):
        create_segment_evidence(setup,'original',start=0,end=1,output=bundle)
    assert (bundle/'clip.mp4').read_bytes()==b'foreign video'
    assert (bundle/'manifest.json').read_bytes()==b'foreign manifest'
    assert not list(tmp_path.glob('.facut-evidence-*'))


def test_delayed_audio_alignment_and_tones(setup,tmp_path):
    import subprocess,array,math
    source=Path(setup.document.media[0].path)
    tones=tmp_path/'tones.wav'
    ff=find_executable('ffmpeg')
    run_tool([ff,'-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=1',
              '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=1',
              '-filter_complex','[0:a][1:a]concat=n=2:v=0:a=1[a]','-map','[a]',tones])
    run_tool([ff,'-v','error','-y','-f','lavfi','-i','testsrc2=s=160x120:r=25:d=3',
              '-itsoffset','0.5','-i',tones,'-c:v','libx264','-threads','1','-c:a','aac',source])
    no=create_segment_evidence(setup,'original',start=0,end=.25)
    assert no['audio']['status']=='outside_selected_range'
    assert no['output']['technical']['audio_codec'] is None
    tail=create_segment_evidence(setup,'original',start=2.8,end=3)
    assert tail['audio']['status']=='outside_selected_range'
    data=create_segment_evidence(setup,'original',start=.25,end=2.25)
    raw=subprocess.run([str(ff),'-v','error','-i',data['output']['video'],'-vn','-ac','1','-ar','48000','-f','f32le','-'],check=True,capture_output=True).stdout
    samples=array.array('f',raw)
    def section(a,b):return samples[int(a*48000):int(b*48000)]
    def rms(x):return math.sqrt(sum(v*v for v in x)/len(x))
    assert rms(section(.03,.18))<.001
    assert rms(section(.30,.45))>.03
    for a,b,hz in [(.4,.9,440),(1.4,1.9,880)]:
        x=section(a,b)
        measured=sum(x[i]<=0<x[i+1] for i in range(len(x)-1))*48000/len(x)
        assert abs(measured-hz)<10
