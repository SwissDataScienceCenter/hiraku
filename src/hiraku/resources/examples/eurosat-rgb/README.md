# EuroSAT RGB Example

This example provides the dataset and training scaffold for comparing
architectural inductive biases on RGB satellite images. The starter does not
include a model; review the literature, implement and run the flattened-pixel
MLP baseline, then use the evidence and baseline results to select follow-up
architectures. Keep the split, optimizer, seed, and training budget fixed
across experiments.

The project files copied by `hiraku init --example eurosat-rgb` are stored in
`src/`. See `docs/examples/eurosat-rgb/README.md` in the Hiraku repository for
setup and the research workflow. The first training run downloads EuroSAT RGB
through `torchvision.datasets.EuroSAT`.
