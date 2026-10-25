# Interface design review

Reviewed on 25 October 2026 against the rendered local app. The supplied Design Lead and audit briefs asked for a clear interface, readable hierarchy, accessible interactions and visible system states. The review kept the existing visual language and addressed the observed navigation and control-placement problems.

## User goals and page order

The main paths are to play, inspect a saved run, test one suggestion, review an answer and compare learning or provider options. The page now follows that order:

1. Title and section navigation.
2. Dinosaur game.
3. Local play controls and saved runs.
4. Fixed-course suggestion comparison.
5. Corrections and compact reviews.
6. Memory and local training evidence.
7. Provider comparison.

The previous insertion point placed several review panels ahead of the local play controls. Moving the comparison after `.lab-controls` restores the relationship between the game, its Start control and the evidence it produces. Five native anchor links let a returning user reach a later section without scrolling through every preceding panel.

![Game and local controls near the top of the application](media/overview.png)

## Visual decisions

The interface retains system fonts, a light neutral page, white work areas and restrained green interaction accents. Existing game sprites, Canvas behaviour and game styling remain intact. The local-controls area uses two columns on a wider screen and one column below its narrow-layout breakpoint.

The new navigation uses the existing colour and spacing approach. Its links wrap into rows when space is limited and retain a visible keyboard-focus outline. The layout was checked at 1280 × 720, 390 pixels wide and 320 pixels wide. Neither narrow check produced horizontal page overflow. At 390 pixels, navigation link targets measured 46 CSS pixels tall.

The supplied examples named Tailwind, Framer Motion, bento layouts, glass effects and kinetic typography. The current CSS already supports the required panels and responsive layout. The observed problems concerned where controls appeared and how events reached the game; a framework migration or decorative animation would not address those causes.

## Interaction and accessibility checks

| Element or state | Review result |
| --- | --- |
| Navigation | Five labelled native links target existing sections. The controls remain keyboard focusable through native behaviour. |
| Game input separation | A browser check confirmed that Space used in navigation no longer starts the game. |
| Focus styling | The navigation and existing workbench controls define visible focus outlines in CSS. |
| Local play | Start appears in the initial desktop view; its top measured 535.68 CSS pixels at 1280 × 720. |
| Saved replay | The button changes between Play and Pause; the slider has an accessible name and sampled images have descriptive alternative text. |
| Empty history | The interface tells the user to start a local run when there are no saved runs. |
| Loading | Source inspection confirms disabled refresh while loading and a saved-run loading message. |
| Refresh failure | The warning identifies previously loaded rows, explains that refresh failed and names Refresh runs as the next action. It uses an alert region. |
| Server return | A refreshed empty demo store displayed its start-a-run instruction and cleared the old refresh and selection alerts. |
| Provider state | The interface labels connection readiness separately from inference success and distinguishes local rules from provider use. |

The checks above are specific observations and source findings. They do not establish full screen-reader support, colour-contrast compliance across every control, every keyboard path or a sub-100-ms response time. Those remain separate acceptance tasks.

## Trust in results

The review preserves several distinctions that affect the interface:

- Local rules can play while Mubit is disconnected.
- A saved screenshot replay contains sampled images, not a continuous recording.
- A previous result can remain on screen after refresh fails, provided the warning identifies it as previously loaded.
- Saving a correction does not establish training.
- Approved references added to a prompt and local model-weight changes have separate evidence.
- A provider request with an unknown outcome must not appear successful or become an automatic retry.

Inline status and error messages are appropriate for these flows. Skeletons, optimistic completion and transient toast messages were not imposed as universal requirements. An inference or approval needs a confirmed result before the interface can claim it succeeded.

## Media and architecture

The [mobile screenshot](media/mobile.png), [review screenshot](media/review-workflow.png) and [saved-run screenshot](media/saved-run.png) show the actual application with isolated temporary records. The [local-play GIF](media/local-play.gif) shows local gameplay. No provider was contacted during capture.

The standalone architecture map supports search, route tracing, focus and guided views. The inline explorer provides four workflow choices and selectable components. Its narrow-layout and keyboard checks were separate from the application checks. Neither visual presents the Mubit-hosted model as a verified training result.

## Remaining design checks

The current evidence supports the changed layout and the exercised flows. A broader review should test screen readers, zoom, every keyboard path and all provider error states. New interaction states should retain clear source labels and recovery instructions. Any later visual redesign should be evaluated against those tasks rather than a target aesthetic score.

The [audit](AUDIT.md) records observed behaviour and final validation status. [PLAN.md](../PLAN.md) records the delivery checklist.
