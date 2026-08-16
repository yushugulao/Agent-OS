# AgentOS file-query measurements

16 real QEMU boots, balanced AB/BA order. Each boot runs a reference directory traversal and the indexed/reused path against the same 96-record corpus.

## Median comparison

| Path | Core (us) | End-to-end (us) | Records examined | Bytes read | Directory probes | Directory entries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| traversal | 121206 | 786683.5 | 385 | 49773 | 3554 | 197090 |
| indexed/reused path | 19499.5 | 697806.5 | 5 | 0 | 3266 | 196802 |

Reference/adaptive core ratio: `6.216`. Reference/adaptive records ratio: `77.0`.
The adaptive path was faster in `16/16` paired boots; the paired median adaptive-minus-reference core delta was `-105667.5 us`.
The complete adaptive workflow was faster in `16/16` paired boots; the paired median end-to-end delta was `-89781.5 us`.

## Catalog build and reuse

The adaptive lane registered `96` records in `6` batch calls, built the Catalog once, and reused it for `4` of `5` total queries.
Median cold build time was `11171.5 us`; median aggregate query time was `3495 us`, including `515 us` in reused queries.

## Raw paired measurements

| Boot | Order | Traversal core (us) | Indexed/reused core (us) | Traversal records | Indexed/reused records | Traversal bytes | Indexed/reused bytes |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | traversal_then_indexed_reused | 116793 | 29249 | 385 | 5 | 49773 | 0 |
| 2 | indexed_reused_then_traversal | 109711 | 7275 | 385 | 5 | 49773 | 0 |
| 3 | traversal_then_indexed_reused | 105204 | 19242 | 385 | 5 | 49773 | 0 |
| 4 | indexed_reused_then_traversal | 112410 | 19913 | 385 | 5 | 49773 | 0 |
| 5 | traversal_then_indexed_reused | 104929 | 7171 | 385 | 5 | 49773 | 0 |
| 6 | indexed_reused_then_traversal | 123297 | 29675 | 385 | 5 | 49773 | 0 |
| 7 | traversal_then_indexed_reused | 122645 | 7679 | 385 | 5 | 49773 | 0 |
| 8 | indexed_reused_then_traversal | 130619 | 20084 | 385 | 5 | 49773 | 0 |
| 9 | traversal_then_indexed_reused | 119767 | 5522 | 385 | 5 | 49773 | 0 |
| 10 | indexed_reused_then_traversal | 125983 | 20307 | 385 | 5 | 49773 | 0 |
| 11 | traversal_then_indexed_reused | 148926 | 6369 | 385 | 5 | 49773 | 0 |
| 12 | indexed_reused_then_traversal | 118247 | 20437 | 385 | 5 | 49773 | 0 |
| 13 | traversal_then_indexed_reused | 116978 | 6599 | 385 | 5 | 49773 | 0 |
| 14 | indexed_reused_then_traversal | 126181 | 20522 | 385 | 5 | 49773 | 0 |
| 15 | traversal_then_indexed_reused | 130553 | 13598 | 385 | 5 | 49773 | 0 |
| 16 | indexed_reused_then_traversal | 177465 | 19757 | 385 | 5 | 49773 | 0 |

Both paths produced the same verified outcome: `recovered` (hash `1457873431608088591`).

The original QEMU logs remain beside this report; measurements.csv contains the per-boot numeric rows.
