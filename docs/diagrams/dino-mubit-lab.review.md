# Architecture map verification

Refreshed on 25 October 2026 from [README.md](../../README.md) and [ARCHITECTURE.md](../../ARCHITECTURE.md), with implementation evidence pinned to commit `a4bd5eae0a7f93a34c85572f23827bb65632e1ee`. The documents and code agree on nine components and eleven directed relationships. It groups private evidence stores into one component and treats local MLX training as a separate command. It does not claim live traffic, provider health or a Mubit model update.

## Changes in this refresh

The source pin and local-service source range now include the current request-validation boundary. Three added source references point to the README purpose and learning limits, the architecture overview, and its distinction between memory and local training. Token usage is labelled as reported by providers while response time is measured locally; missing fields remain unknown. The unverified hosted-training route is scoped to this project.

Both requested documents now contain the rendered preview and retain their Mermaid overviews in collapsible sections. The existing component IDs, relationships, layout and three guided views were retained. The temporary capture server is a mode of the local service, not an extra production component.

## Artifact identity

| Item | SHA-256 | Bytes |
| --- | --- | ---: |
| Editable architecture JSON | `27a7a0797d5fa9465a7931a6b49683a768af20e64b5bc8b8ff833ae9d06028c4` | 9,816 |
| Interactive HTML | `129fdbcc2b372a8b31d432b49658606c8198f8740a3dfa8cf904816a02bed33d` | 818,160 |

The [delivery receipt](dino-mubit-lab.delivery.json) verifies 24 source references against committed blobs. Its 9 artifact checks passed under the showcase profile with zero composition errors and zero warnings. The source and artifact hashes bind the exact delivered files.

## Browser evidence

Archify's isolated Chrome check passed at 1440 by 900, 1600 by 1000, 1920 by 1080 and 2048 by 1320 pixels. At each size, the document fit the viewport without horizontal or vertical overflow. The automated checks also passed their node-text and viewer-control containment criteria.

Light and dark screenshots were captured at 1440 by 900 and 2048 by 1320. The [browser receipt](dino-mubit-lab.visual-check.json) is bound to the same HTML hash. The [contact sheet](dino-mubit-lab.visual-check.html) links the four captures.

## Perceptual review

An image-capable reviewer inspected all four captured images. Node names, relationship labels and cards fit their containers. The local boundary separates Mubit and OpenAI from the local service. Routes do not cross unrelated nodes, and the largest view uses the page height for the diagram and its three explanatory cards. Both colour themes preserve the same meaning.

Visual review passed. No visual correction round was needed after delivery. No composition repair was needed for this refresh. The existing checked layout was retained. The automated browser receipt deliberately retains `visualReview: pending`; this separate report records the completed perceptual review.

## Interaction scope

The delivered HTML was also checked in the in-app browser through an authorised loopback preview at `http://127.0.0.1:4042/architecture.html`. The preview copy was byte-identical to the delivered HTML. These interactions were observed:

- Searching for `Mubit` returned 3 of the 9 nodes.
- Selecting Mubit bridge set `#focus=bridge` and showed the current commit and its new ARCHITECTURE source link. Its Close control worked.
- Choosing a route from Browser to Mubit API showed Browser, Local service, Mubit bridge and Mubit API, with 4 nodes and 3 directed hops.
- Selecting the second guided chapter set `#view=local-learning`, showed 2 stops and explained that local weights are separate from Mubit.
- Selecting the provider-comparison chapter showed 3 stops and the revised reported-usage wording. Mubit API and OpenAI API were correctly labelled as grouped, with no direct authored relationship between them.
- No console warnings or errors were reported during these checks.

The [route screenshot](dino-mubit-lab.interactions.png) shows the observed route and its count. Its SHA-256 is `7a95396070f7302717bfdae8d22f2eb9c1332e8191c10f1d5c1f16eb643a04b2`. It was captured after the route camera settled, with all four route nodes visible, then converted from the browser's JPEG capture to PNG without changing the page content, and the resulting image was inspected. The [interaction record](dino-mubit-lab.interactions.json) binds these checks to the delivered HTML and capture.

The checks cover the listed controls only. They do not establish every viewer action, live service traffic, provider health or model activity. The default view is static; readers choose any guided playback themselves.

## Publication notes

Local workspace prefixes in the public validation, delivery and browser receipts are replaced with repository-relative paths. This changes path presentation only. The recorded artifact and specification hashes, validation counts and viewport measurements are unchanged.

The screenshots show the architecture viewer, not the application running a provider request. They contain no keys or private game records.

## Handoff status

```text
diagram_type: architecture
output: docs/diagrams/dino-mubit-lab.html
specification_sha256: 27a7a0797d5fa9465a7931a6b49683a768af20e64b5bc8b8ff833ae9d06028c4
artifact_sha256: 129fdbcc2b372a8b31d432b49658606c8198f8740a3dfa8cf904816a02bed33d
validation: 9/9 showcase, 0 errors, 0 warnings
browser_evidence: passed
visual_review: passed
correction_rounds: 0
```
