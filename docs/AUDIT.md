# Visual and functional audit

Reviewed on 25 October 2026. This audit covers the local application, its published demonstration media and the architecture explorers. It applies the supplied audit brief to the current Python and browser JavaScript implementation.

The browser checks used the real app on a separate loopback server at port 4041 with temporary records. Provider access and Keychain access were disabled. No model request or training run was made, and the original application's records were not used for the captures.

## Result by area

| Area | Result within this review |
| --- | --- |
| Visual layout | The local play controls now precede the experiment and review panels. Desktop and two narrow viewport checks found no horizontal page overflow. |
| Functionality | A normal-speed local run, saved evidence, sampled replay controls, section navigation and architecture interactions were exercised. |
| Trust and recovery | Captures distinguish local rules from Mubit. An offline refresh labels previously loaded rows and gives a recovery action. Restarting the isolated demo and refreshing cleared the old errors. |

No numerical design score is assigned. The template's 9/10 threshold is not a measured usability standard, and this review does not certify the whole application as defect-free.

## Reproduced findings and fixes

### Local play was buried below the review workflow

The [initial page](media/before-layout.png) inserted experiment, correction, learning and provider-comparison panels between the game and its local controls. This placed **Start local run** and the saved-runs panel below the longer review workflow.

The experiment panel now follows `.lab-controls` in [`public/experiment.js`](../public/experiment.js). The game and local controls remain together. Five native section links in [`public/workbench.js`](../public/workbench.js) provide direct access to Play, Compare advice, Review answers, Learning modes and Compare providers.

At a 1280 × 720 browser viewport, the Start control's top was measured at 535.68 CSS pixels. Checks at 320 and 390 CSS pixels found no horizontal page overflow. Navigation link targets measured 46 CSS pixels tall at 390 pixels wide. See [the overview](media/overview.png) and [the mobile capture](media/mobile.png).

### Navigation input could reach the game

The game listens for input outside the navigation controls. A Space event from navigation could bubble to that listener and start gameplay.

The section navigation now stops keyboard, mouse and touch events from propagating into the game while retaining native links. A browser check pressed Space in the navigation and confirmed that the game's start hint remained visible. This check covers that interaction; it is not a complete keyboard or screen-reader audit.

### An offline refresh showed an unclear error beside old results

Stopping the temporary server and choosing **Refresh runs** reproduced a raw `Failed to fetch` message. Previously loaded rows remained visible without a clear statement that refresh had failed.

The workbench now preserves those rows and reports:

> Previously loaded results are shown. Run history could not be refreshed. Check the local server, then choose Refresh runs.

The message uses an alert region and the Refresh control becomes available again. The final retest saved a real temporary run with score 36 over 4.4 seconds, then stopped only the isolated server. Refresh retained the previous row with the warning. Opening that run showed a separate, clear loading failure. The [error capture](media/refresh-error.png) records both messages.

Restarting the demo creates a new temporary store. Refresh then showed the empty-history instruction and cleared the old error alerts. This verifies interface recovery after reconnecting to an empty demo store; it does not establish recovery of a missing durable record.

### Request parsing accepted ambiguous input

The backend review reproduced duplicate JSON members, non-finite numbers, excessive nesting and an early end of the request body reaching a mocked route. The common parser now rejects these cases before configuration reads or action execution. The [debugging report](DEBUG.md) contains the root cause, exact cases and regression evidence.

## Browser checks

| Check | Observation |
| --- | --- |
| Desktop layout | Game and local controls appear before the longer experiment workflow at 1280 × 720. |
| Narrow layouts | No horizontal page overflow at 320 or 390 CSS pixels. |
| Section navigation | Native links target the five named sections; Space in navigation did not start the game. |
| Local gameplay | A rule-based run completed at normal speed with score 245 after 22.7 seconds, ending in a collision. |
| Saved evidence | That run recorded 32 actions and 29 sampled images. |
| Replay controls | **Play sampled replay** and **Pause replay** states worked. Sampled images are labelled as sampled evidence rather than continuous video. |
| Offline history refresh | Previously loaded results stayed visible with an explicit warning and a retry instruction. |
| Server return | Restarting the isolated demo and refreshing displayed an empty history with no old error alerts. |
| Architecture map | Search matched three of nine components for Mubit; route tracing followed Browser → Local service → Mubit bridge → Mubit API. Focus and a two-chapter guided view worked. |
| Inline architecture explorer | All four workflows worked. OpenAI selection persisted on reload, Enter selected Private files, and 320/736-pixel checks found no overflow, console warnings or errors. |

The local run score is one observation on a random course. It is not a controlled performance comparison or evidence that the player improved.

## Loading, empty, error and success states

The source provides a disabled Refresh control while a request is in flight, a loading message while a saved run is read, and an empty-history message that directs the user to start a local run. A completed local run appeared with its saved measurements and replay controls. Error messages now distinguish a failed refresh from the previously loaded results still on screen.

These checks support the specific states described above. Input-to-feedback latency below 100 ms was not measured. The app uses inline status text rather than adding skeletons or toast messages solely to match the generic template.

Provider success and approved records must remain tied to a confirmed response. Optimistic success messages or automatic inference retries would misrepresent uncertain outcomes. The [error-handling review](ERROR-HANDLING.md) records the relevant boundaries.

## Captures and architecture validation

The published [local-play GIF](media/local-play.gif) is a 16-second excerpt of the real local app, at 584 × 720 pixels with 192 frames. Its size is 821,739 bytes. The complete file passed decoding; visual inspection sampled seconds 0, 8 and 15. It shows local play and does not show Mubit making decisions or training a model.

Still captures cover the [overview](media/overview.png), [mobile layout](media/mobile.png), [review workflow](media/review-workflow.png), [saved run](media/saved-run.png), [local play](media/local-play.png), initial layout and recovery guidance. All eight published media files were inspected. The saved-run still precedes the final singular-run wording fix. The [capture record](media/capture-record.json) identifies each file, its hash, source details and checks.

The Archify map passed all nine showcase checks with zero errors or warnings and 21 source references pinned to a committed code snapshot. Four viewport checks passed. The [delivery receipt](diagrams/dino-mubit-lab.delivery.json), [visual review](diagrams/dino-mubit-lab.review.md) and [interaction record](diagrams/dino-mubit-lab.interactions.json) preserve those distinct checks.

## Regression checks

The final integrated checks passed 288 Python tests and 83 JavaScript tests. The strict-parser review also passed its 41-test route suite, and five isolated recovery branches passed after the final workbench change. These automated checks use temporary data and synthetic failures; the browser observations above are separate evidence. [PLAN.md](../PLAN.md) records the delivery checklist.

## Scope limits

This was a bounded local review. It did not test every possible saved record, every browser, all assistive technologies, a full WCAG conformance set, every provider failure, production load or a public deployment. No live Mubit response, direct OpenAI response, memory-assisted answer or model update was generated during the audit.

Glass effects, animated typography, a new CSS framework and serverless infrastructure were not added. They were examples in the supplied briefs, and no observed defect required them. The [design review](DESIGN.md) explains the visual decisions and the [attachment coverage record](ATTACHMENT-COVERAGE.md) maps the completed work to each source brief.
