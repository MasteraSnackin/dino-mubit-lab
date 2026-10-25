# Architecture map verification

The map describes the implemented local service and its external dependencies at commit `556db3cfc4c60b6cef6738244404d74b7b558870`. It groups private evidence stores into one component and treats local MLX training as a separate command. It does not claim live traffic, provider health or a Mubit model update.

## Artifact identity

| Item | SHA-256 | Bytes |
| --- | --- | ---: |
| Editable architecture JSON | `69fc3a7858ed978236ba12dc0605cf22149058c635493086750259364b133b0f` | 9,297 |
| Interactive HTML | `a41edf878389a522695bf6d0c50de43ff557f7ffefeea3f1fd7cc449e752c74f` | 817,466 |

The [delivery receipt](dino-mubit-lab.delivery.json) verifies 21 source references against committed blobs. Its 9 artifact checks passed under the showcase profile with zero composition errors and zero warnings. The source and artifact hashes bind the exact delivered files.

## Browser evidence

Archify's isolated Chrome check passed at 1440 by 900, 1600 by 1000, 1920 by 1080 and 2048 by 1320 pixels. At each size, the document fit the viewport without horizontal or vertical overflow. The automated checks also passed their node-text and viewer-control containment criteria.

Light and dark screenshots were captured at 1440 by 900 and 2048 by 1320. The [browser receipt](dino-mubit-lab.visual-check.json) is bound to the same HTML hash. The [contact sheet](dino-mubit-lab.visual-check.html) links the four captures.

## Perceptual review

An image-capable reviewer inspected all four captured images. Node names, relationship labels and cards fit their containers. The local boundary separates Mubit and OpenAI from the local service. Routes do not cross unrelated nodes, and the largest view uses the page height for the diagram and its three explanatory cards. Both colour themes preserve the same meaning.

Visual review passed. No visual correction round was needed after delivery. Two earlier authoring repairs resolved label clearance and one overlong node description before the final validation.

## Interaction scope

The delivered HTML was also checked in the in-app browser through an authorised loopback preview at `http://127.0.0.1:4042/architecture.html`. The preview copy was byte-identical to the delivered HTML. These interactions were observed:

- Searching for `Mubit` returned 3 of the 9 nodes.
- Selecting Mubit bridge set `#focus=bridge` and showed its verified source links.
- Choosing a route from Browser to Mubit API showed Browser, Local service, Mubit bridge and Mubit API, with 4 nodes and 3 directed hops.
- Selecting the second guided chapter set `#view=local-learning`, showed 2 stops and explained that local weights are separate from Mubit.

The [route screenshot](dino-mubit-lab.interactions.png) shows the observed route and its count. Its SHA-256 is `73f8f95c0cea08600e05a9299619c2e9f143e04a1b2aae339373015ffd71433c`. It was converted from the browser's JPEG capture to PNG without changing the page content, and the resulting image was inspected. The [interaction record](dino-mubit-lab.interactions.json) binds these checks to the delivered HTML and capture.

An earlier, separate Playwright attempt was blocked by that tool's `file:` protocol restriction and did not perform any interaction checks. The later checks used the authorised in-app browser preview. This sequence is recorded separately from Archify's automated browser result, which remains passed.

The checks cover the listed controls only. They do not establish every viewer action, live service traffic, provider health or model activity. The default view is static; readers choose any guided playback themselves.

## Publication notes

Local workspace prefixes in the public validation, delivery and browser receipts are replaced with repository-relative paths. This changes path presentation only. The recorded artifact and specification hashes, validation counts and viewport measurements are unchanged.

The screenshots show the architecture viewer, not the application running a provider request. They contain no keys or private game records.
