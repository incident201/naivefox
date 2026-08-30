# Browser-diversity benchmark

This is an additional experiment, not a replacement for the matched-page
`CAPTURE.md` gate or its packet-window residuals. The question is whether the
observed outer flow is distinguishable from a broader, declared population of
Firefox page loads, including previously unseen page families.

## Fixed transports and default

The new transport's experimental default is `continuous-bulk-pipeline`.
Omitted Caddy profile, bridge continuous/window settings and the experimental
runner agree on that choice. Explicit historical profiles remain available.
Native `naivefox-full-source` defaults and stock-compatible CONNECT behavior
are unchanged; no export, Firefox browser rebuild or release is part of this
benchmark. The retained same-base native build and official Firefox reference
are reused, with hashes/source references recorded in generated provenance.

The measurement corpus does not change the operator fronting-site contract.
Classic H3 still needs its documented six-resource page, and No Connect still
needs the dedicated module/SPA protocol. Neither accepts an arbitrary corpus
page as a drop-in transport server. The corpus is normal browser/inner workload,
not a new padding profile or a change to production scheduling.

## History preflight

The repository already has a passive multi-workload logistic classifier with
train-only preprocessing, Firefox A/B controls and grouped experiment-block
cross-validation. The current multi-arm p17--32/Whole report is instead a
model-free distance to matched Firefox A/B observations. Existing fixed-page,
resource-size and lifecycle campaigns do not measure a page-family-disjoint
background corpus. This experiment reuses their capture health and feature
logic, not their paired distance as its classification score.

## Corpus and collection

- 96 deterministic synthetic pages: 24 archetype families with four variations
  each. Examples include documents, articles, galleries, shops, forums, forms,
  dashboards and API-driven applications. Actual HTML/CSS/JS/image/data
  dependencies create the graphs; random transport padding does not stand in
  for ordinary resources. Assets, seed, graph metadata and hashes are frozen
  before classifier results are inspected.
- Variants change meaningful content/card/table/image counts and dependency
  graphs. They are not 96 independent site families. Family is the split and
  uncertainty unit; no variant of a held-out family enters fitting/calibration.
- Primary local frontend is SOCKS. H2 and H3 are separate experiments with
  identical same-base inputs, isolated WSL namespace, MTU/offload and capture
  health rules. No protocol mixing lets a classifier learn H2 versus H3 instead
  of browser versus proxy. HTTP-listener generalization is not inferred.
- Per page instance: independent Firefox A/B visits, classic native CONNECT,
  and No Connect. Native and No Connect carry the same inner workload. The
  direct-browser page and inner page are permuted independently within the
  preassigned held-out family partition, so the outer browser background is
  not the exact fronting page or a matched trace used for scoring.
- Additional genuine Firefox visits to the actual fronting SPA are a separate
  diagnostic: flagging both a proxy and that real browser equally is evidence
  of surface/corpus mismatch, not proof of a non-browser stack.
- Fresh profiles and pre-launched browser readiness, one measured origin flow,
  valid certificates, strict H3 without TCP fallback, fixed capture duration,
  and no process shutdown before capture ends. Single-origin mirroring,
  controlled cache state and synthetic content are explicit limitations.
- Run a small unscored infrastructure pilot first. The main schedule is fixed
  and randomized before capture. Invalid capture/transport runs are recorded;
  failures cannot be discarded based on their classifier score.

Generated corpus/fixtures live in the retained Linux object directory, not new
top-level home directories. Successful samples retain numeric features,
admission/provenance and workload metadata; raw captures, credentials and
browser profiles are removed after extraction. No archives are created.

## Analysis contract

Split the 24 families into four fixed partitions. For each outer evaluation,
one partition is test, another is threshold calibration and the other two are
training. Rotate so every family is tested once. Every content variant and
every transport observation tied to those families follows that split.
Fit all normalization, feature screening and model parameters on training data
only. Select thresholds on calibration data, never on test labels. This follows
the [grouped holdout and preprocessing rules](https://scikit-learn.org/stable/modules/cross_validation.html).

Use only the existing passive numeric feature whitelist; exclude endpoints,
URLs, page/family IDs, class/arm labels, absolute timestamps, process/browser
identities, plaintext and decoded HTTP semantics. Analyze the standard five
views separately (p1--16, p17--32, p1--32, 250 ms, whole). Do not tune the
transport or corpus after inspecting held-out performance.

Primary model: the existing regularized logistic learner, fixed feature budget
and training parameters. Report classic-versus-Firefox and No-Connect-versus-
Firefox, oriented AUC without test-set sign reversal, calibration-selected
operating points (target 5% and 10% browser false positives), achieved test FPR,
TPR and balanced accuracy. Include Firefox-A-versus-B as a collection/null
diagnostic, and fronting-browser false positives separately. A positive-only
browser anomaly score may be reported as a separate secondary diagnostic, not
as a probability that traffic came from Firefox.

Use held-out fold metrics and family-clustered uncertainty; intervals conditional
on the fitted models are labelled accordingly. Report false-positive numerators
and denominators, not an unsupported precise 1% claim from a small tail.
[ROC TPR/FPR](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.roc_curve.html)
does not determine real-world precision without a class prevalence assumption.

The result is corpus-specific and screening-only. A broad synthetic background
can make a detector look weaker; one weak classifier does not prove that no
detector exists. Report unfavorable results and surface-control failures too.
Do not translate AUC into the old paired residual or claim Internet-wide
indistinguishability from this benchmark.

## Status

Default selection and explicit-legacy regression checks passed. Corpus,
collector and page-family analysis are being implemented; no diversity score
has been measured yet. The stopped optimization campaign's existing numerical
matrix remains in `APPLICATION-CARRIER-STATUS.md` and is not overwritten by a
new, differently defined benchmark.
