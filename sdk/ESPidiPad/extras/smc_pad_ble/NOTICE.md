# Notice: authorship and sources

This kit was prepared by Ruslan Mazavin, with Claude Opus 5.5 and Claude Opus 4.8 (Anthropic), from the pi32v2_re
research project. It may be used in public projects. When you build on it in public, please credit:

- **pi32v2_re (Ruslan Mazavin)** for facts tagged [firmware] in `PROTOCOL.md`: the pad's firmware read statically
  from a full flash dump of firmware `SMC-PAD_004`, and the GATT map parsed from its ATT database.
- **spectalive/smc-pad**, https://github.com/spectalive/smc-pad, Copyright 2026 Cristian Deluxe, Apache-2.0, for
  facts tagged [app], [capture] and [smc-pad], and for the captured byte sequences used as test vectors in
  `ref/test_smc_pad_protocol.py` (from its `reference/FINDINGS.md` and `reference/gatt_unlock.txt`). No code of it
  is copied here. Its `reference/mac-decompile/` (M-VAVE's code) is not used.
- **Quixotic7/MvaveFM1Unbricker**, https://github.com/Quixotic7/MvaveFM1Unbricker, which documents the ROM-mode
  command `F0 22 24 35 7D F7` for the FM-1.

**M-VAVE** and **SMC-PAD** are names of their owner. No M-VAVE code or firmware is included in this kit.

Licence of the kit's own files: not set yet; ask Ruslan.
