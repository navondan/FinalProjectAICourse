# Eval loop report

Golden set: `GOLDEN-DATASETS.json`. Targets: quality 0.9, each check 0.8, floors forbidden_tools=1.0, forbidden_sources=1.0, forbidden_facts=1.0, noise floor 0.03.

The gate reports a distance to target, not a pass. 0.0 means every target is met.

## Before and after

| | Baseline (round 0) | Best (round 2) |
|---|---|---|
| Build | `c9b7cb9` | `823451a` |
| Quality | 0.686 (227/331) | 0.707 (234/331) |
| Distance | 1.680 | 1.558 |
| Floors broken | forbidden_tools: 0.980 < 1.0 | none |
| expected_sources | 0.095 | 0.095 |
| expected_tools | 0.641 | 0.615 |
| forbidden_facts (floor) | 1.000 | 1.000 |
| forbidden_sources (floor) | 1.000 | 1.000 |
| forbidden_tools (floor) | 0.980 | 1.000 |
| required_facts | 0.441 | 0.542 |
| should_use_tools | 0.557 | 0.582 |
| terminal_status | 0.975 | 0.962 |

## Rounds

| Round | Build | Change | Quality | Distance | Floors broken | Gate | Human decision |
|---|---|---|---|---|---|---|---|
| 0 | `c9b7cb9` | M6.5 build as committed (honest baseline before any loop change) | 0.686 | 1.680 | forbidden_tools: 0.980 < 1.0 | - | baseline |
| 1 | `e44728f` | conversation_recall intent: recall turns answer from history with no tools (fixes S2-onboarding-maya:7 floor break) | 0.725 | 1.390 | forbidden_tools: 0.980 < 1.0 | reject | rejected by Dan Navon: quality up but forbidden_tools floor still broken (S4-constraint-carry:5) |
| 2 | `823451a` | narrow conversation_recall (restating only); planner: read-only lookups are never filing | 0.707 | 1.558 | none | promote | promoted by Dan Navon: restores forbidden_tools floor; quality gain within noise |

Budget left: 3 rounds, 2 stalled rounds.
