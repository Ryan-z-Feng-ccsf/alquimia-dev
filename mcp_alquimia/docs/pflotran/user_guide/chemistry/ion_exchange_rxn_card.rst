ION_EXCHANGE_RXN
================
Specifies parameters for an ion exchange reaction.

Required Cards:
---------------

ION_EXCHANGE_RXN
 Opens the block.

CEC <float>
 Cation exchange capacity in (1) equivalents per volume of mineral
 [eq/m\ :sup:`3`:sub:`mineral`\] (if a MINERAL card is present) or (2)
 equivalents per bulk volume [eq/m\ :sup:`3`:sub:`bulk`\] (if a MINERAL card
 is **not** present)

CATIONS
 Opens the CATIONS block for listing cations participating in the reaction.

  <Cation species Name> <float> <REFERENCE for reference cation only>
    Name of cation and associated selectivity coefficient.  One must select a
    reference cation with a selectivity coefficient of 1 and convert all the
    selectivity coefficients to be relative to the reference species selectivity
    coefficient. The REFERENCE card must be appended to the reference cation
    (see examples). The entered value is the half-reaction coefficient
    :math:`k`, not :math:`\log_{10} k` and not :math:`1/k`. For a monovalent
    reference cation, :math:`k_i = K_{i,\mathrm{ref}}`. If a published
    selectivity is the per-equivalent coefficient :math:`\tilde K`, enter
    :math:`k_i = (\tilde K)^{z_i}` when the reference valence is 1.

Optional Cards
--------------

MINERAL <string>
 Name of the mineral to which the cations sorb.

Examples
--------
 ::

  SORPTION
    ION_EXCHANGE_RXN
      MINERAL Halite
      CEC 750.  ! eq/m^3
      CATIONS
        Na+   1.d0 REFERENCE
        Ca++  3.38638672536d0  ! (1.84)^2
        Mg++  6.00240096038d0  ! (2.45)^2
      /
    /
  END
