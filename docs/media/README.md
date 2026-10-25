# Demo and application captures

[Back to the project README](../../README.md)

## Recorded walkthrough

[Download the demo MP4](https://github.com/MasteraSnackin/dino-mubit-lab/releases/download/demo-video-v2/Dino-Mubit-Lab-recorded-demo.mp4) or [open the release page](https://github.com/MasteraSnackin/dino-mubit-lab/releases/tag/demo-video-v2). The video is **1 minute 50 seconds**, **1080p** and **7.69 MB**, with George narration, quiet transition effects and selectable English subtitles.

It shows a new local gameplay run and a recorded walkthrough of saved Mubit reviews, answer approval, prompt memory, local training evidence and provider results. Saved results are labelled; the recording does not represent new provider calls or hosted Mubit training.

There is no hosted gameplay demo. Run the local server to use the application.

## Application captures

These images show the real local application with temporary demonstration records. The GIF follows a local rule-based player for 16 seconds at normal speed. It does not show Mubit inference, hosted training or a measured improvement from AI advice.

| File | What it shows |
| --- | --- |
| [Overview](overview.png) | The game, section links, local controls and saved-run list after the layout fix. |
| [Local play GIF](local-play.gif) | A real run, captured at 584 by 720 pixels and encoded at 12 frames per second. |
| [Local play still](local-play.png) | The player jumping during the recorded run. |
| [Saved run](saved-run.png) | Score 245, a collision after 22.7 seconds and sampled replay controls. This still precedes the later singular-run wording fix. |
| [Review workflow](review-workflow.png) | Empty correction records and the explicit unverified-training state. |
| [Mobile layout](mobile.png) | The interface at a 390 by 844 CSS-pixel viewport. |
| [Recovery guidance](refresh-error.png) | Failed refresh and selection, with cached results labelled and a next step shown. |
| [Before layout changes](before-layout.png) | The earlier ordering, where the comparison panel appeared before local play controls. |

The [capture record](capture-record.json) contains dimensions, hashes, source details, observed checks and limits. The complete GIF passed decoding; its first, middle and final seconds were inspected visually. Static captures were also inspected. The saved run is an observation from one random course, not a performance benchmark.

To open a fresh local demonstration without keys or private records:

```sh
python3 scripts/demo_server.py --port 4041
```

Run this command from the repository root. Open `http://127.0.0.1:4041/`, start a local run and keep the tab visible. Provider credentials are unavailable in this demonstration process, and its temporary records are removed when it stops. The ordinary application continues to use `python3 serve.py --open` and its own saved records.
