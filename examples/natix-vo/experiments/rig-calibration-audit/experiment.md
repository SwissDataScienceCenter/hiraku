---
id: rig-calibration-audit
branch: exp/rig-calibration-audit
mode: guided
goal: vo-backend-selection
parent: rig-camera-vo
kind: diagnostic
status: proposed
origin: agent-diagnostic
expectation: "The rig added no improvement because extrinsic calibration or inter-camera time synchronisation error is comparable to the constraint the rig would contribute, cancelling it out."
created: 2026-08-24
---

# Rig calibration and synchronisation audit

## Scope: hypothesis or task

Explanation 1 for `rig-camera-vo`'s refutation. A rig only adds constraint if the relative poses and
the timestamps are accurate enough that the extra views agree with each other. If either is off, the
extra views inject error at roughly the scale of the constraint they add, and the net effect is
nothing — which is exactly the observed outcome.

At a vehicle speed of 50 km/h, 10 ms of inter-camera timing error is about 14 cm of baseline error.
That is the order of magnitude a rig's constraint operates at, which is why this explanation is
plausible rather than a formality.

## Expectation and how it could fail

If calibration or sync is the cause: reprojection residuals between camera pairs will show a
systematic, structured component — constant offset, or one growing with speed — rather than noise.
Re-estimating extrinsics from the data, or correcting the timing offset, should then produce the
improvement the rig was supposed to give.

Refuted if inter-camera residuals are unstructured and at sensor-noise scale, and correction changes
nothing. That would rule this out and point at `rig-overlap-geometry` instead.

**Discriminating**: residual structure (and its dependence on speed) separates the two explanations.
Structured, speed-dependent residuals → calibration or sync. Clean residuals with the rig still
adding nothing → geometry.

## Implementation plan

Measure before changing anything: inter-camera reprojection residuals on existing runs, split by
vehicle speed; the timestamp deltas actually present in the data as opposed to the nominal ones; and
extrinsics re-estimated from the sequences compared against the ones used.

No new runs of the backend are needed for the diagnosis itself — it reads what `rig-camera-vo`
already produced. That is what makes it cheap, and why it should come before any re-run.

## Evaluation protocol

Inherited, unchanged. The diagnosis reads intermediate quantities rather than the goal's metrics; a
correction that follows is measured on the goal's metrics as usual.

## Findings

**[Gap — proposed, not run.]**

## Conclusions

**[Gap.]**

## Decisions and next steps

Investigation test — **conditions 1, 2, 4, 5, 6 hold; condition 3 is answered; the blocking gap is
the parent's Findings.**

1. **Named cause** — extrinsic or temporal calibration error comparable in magnitude to the rig's
   contribution.
2. **Discriminating** — residual structure and speed dependence separate this from
   `rig-overlap-geometry`; the two predict different residual patterns.
3. **Novel** — nothing in this tree has examined rig calibration; the parent varied the backend, not
   the calibration.
4. **In scope** — the rig strategy is in scope for this goal; protocol unchanged.
5. **Within budget** — reads existing outputs; no backend runs for the diagnosis.
6. **Proportionate** — cheap, and it decides whether the rig direction is worth any further runs at
   all.

Held at `proposed` rather than `approved` because condition 2 depends on the parent's Findings: if
the rig result was *worse* than single-camera this is the strong candidate, and if it was exactly flat
`rig-overlap-geometry` is. Filling the parent's Findings picks which of these to run — and possibly
tells you to run neither.

## Children

None.
