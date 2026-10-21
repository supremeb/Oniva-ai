# Judge calibration labels

One `<judge>.jsonl` per judge in `evals/judges/`: 20 `yes` and 20 `no` examples each (200 rows; `recall_probe` has 25
pairs, the last five on early-ID probes for the long-session suite), the minimum per
class that `python -m evals.calibrate` requires.

## Every label here is a DRAFT and needs human review

These rows were written by an AI assistant, not verified by a person. Each row carries `"reviewed": false` and a
`review_note`. Calibration measures a judge against human ground truth (KTD6), so unverified labels measure nothing.

- `python -m evals.calibrate` refuses to run on a set that has any `reviewed: false` row. `--allow-unreviewed` runs it
  as a dry run and writes no `<judge>.record.json`, so a draft set can never make a judge gating.
- A judge may gate only after a person has read every row, fixed or deleted the ones they disagree with, set
  `"reviewed": true` on each row they accept, and a fresh calibration (TPR and TNR both at least 0.85) has written the
  record.
- Rows are paired: `<prefix>-NN-yes` is a correct/supported/covered example and `<prefix>-NN-no` is the same scenario
  with one controlled defect, named in `draft_rationale`. Review the pairing, not just the label: if the defect is
  not a real failure under the judge prompt, the label is wrong.
- Pairs make the set easy to learn from. Add harder, unpaired examples from real failed trials while reviewing.
- Known borderline rows to look at first: `recall_probe` rows whose answer hedges but states the right value, and
  `claim_supported` rows that need a small inference from the evidence.

`claim_supported.jsonl` also holds 12 rows (`cs-sa-*`, rationale prefixed `subagent-suite (U24)`) written for the
Deep Work subagent suite: claims whose attribution was swapped between two workers' learnings, claims that merge a
second subject into one learning, claims that add an unsupported detail, and a citation that maps to no learning. They
are `reviewed: false` like the rest and need the same human pass before the judge can gate Gate C's unsupported-claim
criterion.

Row format: `{"id", "label": "yes"|"no", "input": {<the judge's fields>}, "reviewed", "review_note", "draft_rationale"}`.
Input fields per judge are in `evals/graders/judge.py` (`JUDGE_FIELDS`). All text is synthetic; no real data.
