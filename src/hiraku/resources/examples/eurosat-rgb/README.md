# EuroSAT RGB Example

This example compares two architectural inductive biases for RGB satellite
image classification: an MLP over flattened pixels and a shallow CNN. Both use
the same stratified split, optimizer, seed, and fixed number of training epochs.

The project files copied by `hiraku init --example eurosat-rgb` are stored in
`src/`. See `docs/examples/eurosat-rgb/README.md` in the Hiraku repository for
setup and the research workflow. The first training run downloads EuroSAT RGB
through `torchvision.datasets.EuroSAT`.
