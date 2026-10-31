# CrunchFlow sorption

## ION_EXCHANGE

Exchange is described by a mass action expression with an equilibrium constant (Vanselow,
1932; Sposito, 1981; Appelo and Postma, 1993):

$$v\,ACl_u(aq) + u\,BX_v(s) \leftrightarrow u\,BCl_v(aq) + v\,AX_u(s)$$

$$K_{eq} = \frac{(BCl_v)^u (AX_u)^v}{(ACl_u)^v (BX_v)^u}$$

where X is the exchange site occupied by cations $A^{u+}$ and $B^{v+}$, and parentheses are
thermodynamic activities.

### Activity conventions

Gaines-Thomas (Appelo and Postma, 1993), written for Cs+ against a competing cation M (Na+,
K+, Ca++) of charge m on exchange site X(i), each site having charge -1:

$$Cs^+ + \tfrac{1}{m}MX(i)_m \leftrightarrow CsX(i) + \tfrac{1}{m}M^{m+}$$

Adsorbed-species activities are the charge-equivalent fractions

$$\beta(i)_M = \frac{z_M\,q(i)_M}{\sum_M z_M\,q(i)_M} = [X(i)_M]$$

where $z_M$ is the cation charge and $q(i)_M$ the adsorbed concentration of M on site i
(mol/g). Gapon is a variant obtained by writing every reaction with a single exchanger.
Vanselow uses mole fractions:

$$\beta(i)_M = \frac{q(i)_M}{\sum_M q(i)_M} = [X(i)_M]$$

Binary Cs-M mass action:

$$K_{M/Cs} = \frac{\beta(i)_M^{1/m}\,[Cs^+]}{\beta(i)_{Cs}\,[M^{m+}]^{1/m}}
= \frac{[X(i)_M]^{1/m}\,[Cs^+]}{[X(i)_{Cs}]\,[M^{m+}]^{1/m}}$$

Cation exchange capacity, single site and multi-site (Cernik et al., 1996; Voegelin et al.,
2000):

$$CEC = \sum_M z_M q_M, \qquad CEC = \sum_i \sum_M z_M\,q(i)_M$$

### Input

```
ION_EXCHANGE
exchange   Xkaol-   on Kaolinite
convention Gaines-Thomas
END
```

- `exchange <exchanger_name>`: name from the database; several may be listed. Without `on`,
  exchange is on the bulk material and the CEC comes from solid density, porosity and liquid
  saturation. With `on <mineral>`, the CEC follows the mineral volume fraction, which may
  change with time.
- `convention <activity_convention>`: required; `Gaines-Thomas` (Gapon is a variant) or
  `Vanselow`.

The exchanger CEC and solid density are set in each geochemical condition; see
[conditions.md](conditions.md#exchanger-concentrations).

## SURFACE_COMPLEXATION

Follows Dzombak and Morel (1990), with a double-layer (electrostatic) or non-electrostatic
model. Complexation must be on a mineral listed in MINERALS (bulk material is not yet
supported):

```
SURFACE_COMPLEXATION
>FeOH_strong  on  Fe(OH)3
>FeOH_weak    on  Fe(OH)3
END
```

For a non-electrostatic model, append `-no_edl`:

```
>FeOH_strong  on  Fe(OH)3  -no_edl
>FeOH_weak    on  Fe(OH)3  -no_edl
```

Surface hydroxyl site densities are set in each geochemical condition; see
[conditions.md](conditions.md#surface-hydroxyl-concentrations).
