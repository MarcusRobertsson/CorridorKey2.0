# Third-Party Notices

## Community OFX integration

`ofx/` contains a community Windows OpenFX integration for DaVinci Resolve.
Its vendored OpenFX headers and support library were obtained from
`gitcapoom/corridorkey_ofx` at commit
`ef2bdd0c619a34cead9929d49541eb753d3d2df0`. OpenFX is BSD-3-Clause;
see `ofx/plugin/openfx/LICENSE.md`. The v1 reference project's CC BY-NC-SA 4.0
notice is preserved in `ofx/V1-LICENSE.txt`. Optional BiRefNet models and
their custom inference code are published by Peng Zheng and contributors
under MIT; they are downloaded separately from the publisher and are not
bundled. The v2 model and foundation-model notices below still apply.

CorridorKey v2 is licensed under the Corridor Key Licence (see `LICENSE`).
It depends on the third-party components below, which are not owned by
Corridor Digital LLC and remain under their own licenses.

The three foundation models are not redistributed in this repository or in the
CorridorKey Hugging Face repository. `scripts/download_weights.py` fetches them
from their original publishers.

## C-RADIOv4-SO400M

- Publisher: NVIDIA Corporation
- Source: https://huggingface.co/nvidia/C-RADIOv4-SO400M
- License: [NVIDIA Open Model License](https://developer.download.nvidia.com/licenses/nvidia-open-model-license-agreement-june-2024.pdf)
- Use: frozen visual feature extractor, downloaded to `vfms/cradio/`

Licensed by NVIDIA Corporation under the NVIDIA Open Model License.

## MoGe-2 ViT-B

- Publisher: Microsoft Corporation
- Source: https://github.com/microsoft/MoGe, weights at https://huggingface.co/Ruicheng/moge-2-vitb-normal
- License: MIT (code and weights)
- Use: frozen geometry feature extractor. The model code is vendored in
  `third_party/MoGe/` with its license at `third_party/MoGe/LICENSE`; weights
  are downloaded to `vfms/moge/`.

The vendored MoGe code includes DINOv2 (Meta Platforms, Inc.), licensed under
the Apache License 2.0, also reproduced in `third_party/MoGe/LICENSE`.

## RVM (Recurrent Video MAE), small

- Publisher: Google DeepMind
- Source: https://github.com/google-deepmind/representations4d
- License: Apache License 2.0 (code), Creative Commons Attribution 4.0 (weights)
- Use: frozen recurrent video feature extractor. `corridorkey/rvm.py` is a
  PyTorch port of the reference architecture; weights are downloaded to
  `vfms/rvm/`.
