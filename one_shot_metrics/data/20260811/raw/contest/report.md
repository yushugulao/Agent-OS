# AgentOS file-query measurements

16 real QEMU boots, balanced AB/BA order. Each boot runs a directory traversal and the indexed control path against the same 96-record corpus.

## Median comparison

| Path | Core (us) | End-to-end (us) | Records examined | Bytes read | Directory probes | Directory entries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| traversal | 34712.5 | 711283.5 | 97 | 7811 | 3266 | 196802 |
| indexed | 13293.5 | 723928 | 2 | 0 | 3266 | 196802 |

Traversal/indexed core ratio: `2.611`. Traversal/indexed records ratio: `48.5`.
Indexed was faster in `16/16` paired boots; the paired median indexed-minus-traversal core delta was `-23441.5 us`.

## Raw paired measurements

| Boot | Order | Traversal core (us) | Indexed core (us) | Traversal records | Indexed records | Traversal bytes | Indexed bytes |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | traversal_then_indexed | 34971 | 6490 | 97 | 2 | 7811 | 0 |
| 2 | indexed_then_traversal | 39215 | 7783 | 97 | 2 | 7811 | 0 |
| 3 | traversal_then_indexed | 36925 | 5827 | 97 | 2 | 7811 | 0 |
| 4 | indexed_then_traversal | 30814 | 29075 | 97 | 2 | 7811 | 0 |
| 5 | traversal_then_indexed | 36508 | 6551 | 97 | 2 | 7811 | 0 |
| 6 | indexed_then_traversal | 32496 | 20071 | 97 | 2 | 7811 | 0 |
| 7 | traversal_then_indexed | 34383 | 6528 | 97 | 2 | 7811 | 0 |
| 8 | indexed_then_traversal | 49941 | 29020 | 97 | 2 | 7811 | 0 |
| 9 | traversal_then_indexed | 32954 | 18804 | 97 | 2 | 7811 | 0 |
| 10 | indexed_then_traversal | 34454 | 28882 | 97 | 2 | 7811 | 0 |
| 11 | traversal_then_indexed | 31142 | 5180 | 97 | 2 | 7811 | 0 |
| 12 | indexed_then_traversal | 36145 | 29143 | 97 | 2 | 7811 | 0 |
| 13 | traversal_then_indexed | 35983 | 6223 | 97 | 2 | 7811 | 0 |
| 14 | indexed_then_traversal | 37345 | 29005 | 97 | 2 | 7811 | 0 |
| 15 | traversal_then_indexed | 33326 | 18819 | 97 | 2 | 7811 | 0 |
| 16 | indexed_then_traversal | 34309 | 7685 | 97 | 2 | 7811 | 0 |

Both paths produced the same verified outcome: `recovered` (hash `1457873431608088591`).

The original QEMU logs remain beside this report; measurements.csv contains the per-boot numeric rows.
