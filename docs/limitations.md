# Current boundaries

[Documentation](index.md)

| Area | Boundary |
|---|---|
| Creative intelligence | External Agent supplies visual observations and structured decisions; no embedded LLM/general vision model |
| Language requests | `edit` captures feedback, not an autonomous text-to-edit engine |
| v1.0.0 portable Patch output | May report a serialization error after a successful commit; inspect state before retrying. Fixed in this source checkout, not yet in a replacement release package |
| Patch preview | Semantic diff/affected ranges; real A/B renders are separate operations |
| Fast video preview | Current preview graph omits clip effects; use a reduced-resolution render to approve effects/color |
| Stale patches | Currently tied to whole project revision/hash and evidence, not only a dependency subgraph |
| Video encoding | Main final path is H.264/AAC; do not promise every FFmpeg codec through the public renderer |
| Incremental rendering | Transition timelines may fall back to complete rendering; inspect reported warnings |
| HDR/Log | Automatic HDR→SDR tone mapping is not guaranteed; BT.709 delivery may reject these sources |
| Subject reframing | Requires supplied trajectory data; center crop is not intelligent subject tracking |
| Exchange | FCPXML 1.9 is a conservative subset with a loss report; FACUT metadata in OTIO is not proof another NLE interprets it |
| Optional speech | Local providers/models must be configured; no automatic cloud voice upload |
| Platforms | Windows portable distribution; macOS/Linux Python installation; hardware support is host-dependent |
| Music/fonts | User must supply valid licenses; project MIT license does not license imported assets |
| Privacy | Local execution does not prevent a separately operated cloud Agent transmitting its input |

Before delivery, check actual rendered subtitles, fonts, loudness, peaks, source
links, timing, and decode integrity. A test count, a plan, or a successful process
exit is not a substitute for reviewing the output.
