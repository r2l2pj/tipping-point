# 10 — Correções da revisão externa dos índices (TPI)

**Revisão:** externa, modo sênior geoespacial, 15/11/2026 17:30 BRT.
**Base revisada:** `tipping-point-data` em `main` **dc665fd** (15/11/2026 17:26 BRT), rasters servidos da replay v2 (`data/rs_2024/cog`, `data/wa_2025/cog`) e o app em produção (`tippingpoint.l17s.dev`).
**Escopo:** conferir se os índices gerados fazem sentido físico e estatístico e se as alegações públicas se sustentam. Não repete o que as seções 02, 07, 08 e 09 já registram. Os scripts que reproduzem cada número estão no Anexo.

---

## Status (2026-11-15)

| ID | Status | Owner | Where / proof |
|---|---|---|---|
| C1 | **Done** | lead-submission | `app/submission/NUMBERS.md`: every spoken or written number with its `metrics.json` key. RS peak 9,175 km² Critical / 1,627,862 people (May 3) added to the page and slide 6; forecast lift 2.71× (High+) and 1.64× (Critical) already in page, slides, deck and READMEs. The "175 km²" and "55,000" figures appear in no copy we ship (grep in NUMBERS.md) |
| C2 | **Done** | lead-submission (copy), lead-app (radar card) | No "46% within 500 m" in any copy; the scar row reads 3.3× chance at 100 m (`scar_ems_lift_100m` 3.28), 1.1× at 500 m; the app's radar card shows the 100 m line |
| C3 | **Done** | lead-submission | "Rain warns. Radar sees through." in page, README, app, video v7, deck, slides; "Radar confirms" only in historical logs and the original plan |
| C4 | **Done** | lead-submission | RS anchor is the onset: "the run issued 27 h before the first deaths (Apr 29, 2024 00:00 BRT)"; "three days before the peak" appears nowhere |
| C5 | **Done** | lead-app | Now Overview: one basis on both cards plus the Critical share, numbers coloured by the class they count |
| C6 | **Done** | lead-submission | Both limitations in root `README.md` (Declared limitations), `app/README.md` (Limitations), `app/submission/project-page.md` (Limitations; FORM §5 pastes it), slides 6 and the deck |
| M1 | In progress | lead-ml (metrics via lead-analysis) | `*_lift_vs_terrain` for `slide_warned_gfs_24h` (+ Critical) and the flood checks (RS EMS, WA NISAR, Uruguaiana); A3 NASADEM check of WA Critical area above 1,000 m |
| M2 | Evaluating (TPI v3 candidate, not served) | lead-analysis with lead-ml | rain vs snow (IMERG liquid fraction); re-scored on RS / WA / Uruguaiana against v2, adopted only if the acceptance criteria pass |
| M3 | Evaluating (TPI v3 candidate, not served) | lead-analysis with lead-ml | R beyond P99 (to 2×P99); re-scored on RS / WA / Uruguaiana against v2, adopted only if the acceptance criteria pass |
| M4 | Evaluating (TPI v3 candidate, not served) | lead-analysis with lead-ml | class cut-offs re-calibrated across cases; re-scored on RS / WA / Uruguaiana against v2, adopted only if the acceptance criteria pass |
| M5 | Evaluating (TPI v3 candidate, not served) | lead-analysis with lead-ml | HAND 1,000 m + coastal wetland (see also `docs/t-flood-fix-eval.md`); re-scored on RS / WA / Uruguaiana against v2, adopted only if the acceptance criteria pass |
| M6 | Evaluating (TPI v3 candidate, not served) | lead-analysis with lead-ml | S ramp from 0.3 instead of the hard cut at 0.5; re-scored on RS / WA / Uruguaiana against v2, adopted only if the acceptance criteria pass |

## Resumo

| ID | Prioridade | Correção | Onde | Esforço |
|---|---|---|---|---|
| C1 | **P0, antes de submeter** | Atualizar os números do pitch e da submissão para a replay v2 | pitch, `project-page.md`, vídeo | 15 min |
| C2 | **P0** | Retirar "46% dos deslizamentos a menos de 500 m" e usar o lift de 3,28× a 100 m | pitch, submissão, app | 10 min |
| C3 | **P0** | Alinhar o slogan "Rain warns. Radar sees through." em todo material | pitch, submissão, vídeo | 5 min |
| C4 | **P0** | Trocar "três dias antes do pico" por uma âncora no início do evento | pitch, submissão | 5 min |
| C5 | **P0** | Não mostrar "High ou Crítico" e "Crítico" lado a lado sem rótulo no modo Now | app, aba Overview | 30 min |
| C6 | **P0** | Declarar duas limitações novas: índice vira terreno no pico; Washington não separa neve | README, submissão (Limitations) | 15 min |
| M1 | P1 | Comparar todo lift contra uma base só de terreno, não só contra área aleatória | `tools/validation_metrics.py`, `metrics.json` | 2 h |
| M2 | P1 | Separar chuva de neve (fase líquida do IMERG e máscara de neve MODIS) | `gee/tpi.py`, `live/tpi.py`, `tools/replay_v2.py` | 3 h |
| M3 | P1 | Estender R além do P99 para não saturar | `live/tpi.py`, `gee/tpi.py` | 2 h |
| M4 | P1 | Recalibrar os cortes de classe, que ficaram permissivos com a raiz cúbica | `config.yaml`, avaliação | 4 h |
| M5 | P2 | HAND: raio de 1.000 m já validado e tratamento da planície costeira | `config.yaml`, `tools/make_hand90.py` | 2 h |
| M6 | P2 | Rever o corte duro de S em 0,5 | `config.yaml`, `live/tpi.py` | 2 h |

**O que está correto e não precisa mudar**

- O TPI publicado é coerente com R, S e T publicados. Recalculando `(R·S·T)^(1/3)` a partir dos rasters, a classe coincide em 98,6% a 100% dos pixels, conforme o dia.
- As áreas usam a área real do pixel em EPSG:4326, cerca de 0,0070 km² a 30° S, já corrigida pela latitude. A soma independente dá cerca de 9.100 km² Críticos no pico, compatível com `critical_km2 = 9174.578`.
- A replay v2 eliminou os blocos de 10 km e mascarou o Guaíba e a Lagoa dos Patos. Isso foi conferido visualmente.

---

## Achados que motivam as correções

### A1. No pico do evento, o TPI de deslizamento vira um mapa de declividade

Quando a chuva de 3 dias passa do P99 local, R satura em 1. Em RS 2024, **R médio = 1,000 em toda a AOI de 01/05 a 04/05**. Com R e S perto de 1, a raiz cúbica e os cortes 0,2, 0,4 e 0,6 equivalem a limiares fixos de terreno:

| Classe | Deslizamento, com R = S = 1 | Enchente, com R = S = 1 |
|---|---|---|
| Alto ou mais (TPI > 0,4 ⇔ T > 0,064) | declividade > **16,0°** | HAND < 14,4 m e declividade ≤ 5° |
| Crítico (TPI > 0,6 ⇔ T > 0,216) | declividade > **18,2°** | HAND < 12,8 m e declividade ≤ 5° |

Evidência no inventário de 16.027 pontos:

| Banda observada | Área Alto+ | Acerto do TPI | Acerto de um mapa só de declividade, mesma área |
|---|---|---|---|
| 30/04 | 12,74% | 88,7% | 88,8% |
| 02/05 | 14,14% | 90,9% | 90,9% |

No pico, a chuva não acrescenta nada à pergunta "onde". O corte de 16° a 18° é permissivo: 92,7% dos pontos do inventário estão acima de 15°, e 30,9% estão acima de 30°.

### A2. Antes do evento, o terreno sozinho aponta "onde" melhor que o índice

Na rodada GFS emitida em 28/04, a referência de 24 h antes do início usada nas métricas:

| Método | Área sinalizada | Pontos do inventário acertados | Lift |
|---|---|---|---|
| TPI Alto+ publicado (`slide_warned_gfs_24h_lift`) | 2,13% | 5,8% | **2,71×** |
| Mapa estático de declividade com a mesma área | 2,13% | **39,9%** | **18,7×** |

O campo de chuva em 0,1° levou os alertas para onde a anomalia de chuva foi maior, e não para onde os deslizamentos aconteceram. A seção 09 já mostra a causa no nível da chuva: com IMERG Final, a chuva de 3 dias nos pontos ficou abaixo do P90, então R = 0 na maior parte deles.

**Ressalva:** o inventário foi mapeado na Serra, que é íngreme, e isso favorece a base de terreno. Mesmo assim, é a base contra a qual um revisor vai comparar.

**Leitura:** a habilidade de **"quando"** vem da chuva (bate com a seção 07: o ML é um detector de chuva acima do P99). A habilidade de **"onde"** vem do terreno. As alegações públicas precisam separar as duas.

### A3. Washington trata neve como chuva

Não há separação de fase. A única regra de altitude é `coh_max_elev_m = 600`, que vale só para a coerência NISAR. A variável `precipitation` do IMERG inclui neve. Área Crítica de deslizamento em 10/12/2025, por faixa de longitude, usada como proxy de altitude:

| Faixa | Área Crítica | % da faixa em Crítico |
|---|---|---|
| Planície do Puget (oeste de −122,0°) | 18,7 km² | 0,3% |
| Contrafortes (−122,0° a −121,7°) | 752,9 km² | 20,4% |
| **Cascades altas (leste de −121,7°)** | **1.364,4 km²** | **55,7%** |

**64% da área Crítica está nas Cascades altas, em dezembro**, onde muito provavelmente parte da precipitação foi neve. Isso explica cerca de 2.100 a 2.700 km² Críticos (10/12 a 12/12) para 15 deslizamentos mapeados. **Confirmar com o NASADEM** (script no Anexo, item 4) antes de citar.

### A4. R satura cedo

Acima do P99 tudo vale 1, e o P99 se apoia em cerca de 5 das 483 amostras da climatologia. Em eventos extremos, o índice perde toda a gradação de chuva e passa a depender só de S e T.

### A5. Enchente: a planície costeira inteira fica Crítica

A leste da Lagoa dos Patos aparece uma mancha Crítica contínua. Em terreno plano, o proxy de HAND dá T_flood ≈ 1 em toda parte e não distingue banhado de calha de rio. O raio servido é 1.500 m, e o melhor validado contra o MERIT é 1.000 m (seção 02, §4.3).

### A6. S tem um segundo corte duro

Em Washington, o S médio ficou entre 0,36 e 0,46 até 08/12, abaixo de 0,5, o que zera o TPI independentemente da chuva. Na prática o índice tem dois portões duros: P90 da chuva e 0,5 de umidade da zona radicular.

---

## Correções P0 (antes de submeter)

### C1. Números da replay v2 no pitch e na submissão

| Onde aparece hoje | Número antigo (v1) | Usar (v2) | Chave |
|---|---|---|---|
| Pitch, bloco "prova" | "175 km² Críticos três dias antes" | Ler a banda do dia do início. Pela soma independente, a banda de 29/04 tem cerca de 49 km² Críticos de enchente e 1,1 km² de deslizamento | puxar da série diária do bundle, não citar de memória |
| Pitch | "55 mil pessoas expostas" | Recalcular para a mesma banda | idem |
| Pitch e submissão, pico | — | 9.175 km² Críticos e 1,63 milhão de pessoas em área Crítica (pico em 03/05) | `critical_km2`, `people_critical`, `critical_km2_date` |
| Submissão, previsão | "lift 8,76×" | 2,71× (Alto+), 1,64× (Crítico) | `slide_warned_gfs_24h_lift`, `slide_warned_gfs_24h_critical_lift` |

**Critério de aceite:** todo número falado ou escrito tem uma chave do `metrics.json` ao lado no roteiro interno.

### C2. Retirar os 46% a 500 m

O teste contra o EMS (seção 08, §1d) mostra que a 500 m o acerto fica dentro do intervalo aleatório (lift 1,11×). Isso é efeito de terreno, não habilidade.

- **Retirar:** "46% dos 16.027 deslizamentos a menos de 500 m de um candidato".
- **Usar:** "As cicatrizes candidatas acertam 3,3 vezes mais deslizamentos mapeados que o acaso, a 100 m" (`scar_ems_lift_100m = 3.28`, `scar_ems_hit_100m_pct = 27.36`).
- No app, o card do radar ainda mostra a frase de 500 m. Trocar pela de 100 m.

### C3. Slogan

O app usa "Rain warns. Radar sees through." (decisão L42). Atualizar a frase final do pitch, o Summary, o título do vídeo e os slides. "Radar confirms" não aparece em mais nenhum lugar.

### C4. Âncora temporal

"Três dias antes do pico" é tecnicamente verdade, mas o desastre começou em 29/04 às 03Z (L37), e a banda de 29/04 cobre a chuva de 26 a 28/04. Usar "no dia em que o desastre começou" ou "24 h antes do início", com o número correspondente.

### C5. Modo Now: não misturar "Alto ou Crítico" com "Crítico"

Hoje a aba Overview mostra no topo "31.957 pessoas em risco Alto ou Crítico" e, logo abaixo, "Área crítica 0,0 km², 0 pessoas". O código já segue a regra A8 (`live/run_now.py:511-526`). O problema é de texto e layout:

- Rotular os dois cards com a mesma definição, ou mostrar as duas linhas no mesmo card: "Alto ou mais: 31.957 pessoas · Crítico: 0".
- A cor do número segue a classe que ele conta.

### C6. Duas limitações novas na submissão e no README

Texto sugerido para a seção Limitations da submissão:

> During the peak of an extreme event, rain exceeds the local 99th percentile everywhere and the rain score saturates, so the landslide index behaves like a slope map (High above about 16°, Critical above about 18°). Rain drives *when* the index rises; terrain drives *where*.
>
> The index does not yet separate rain from snow. In the Washington case, much of the Critical landslide area falls in the high Cascades in December, where part of the precipitation was likely snow.

---

## Correções de método (P1 e P2, depois da submissão)

### M1. Base de terreno em toda métrica de lift

Toda chave `*_lift` passa a ter uma irmã `*_lift_vs_terrain`: o lift do TPI dividido pelo lift de um mapa só de T com a **mesma área sinalizada**. Valor acima de 1 significa que a chuva e o solo acrescentam algo ao "onde".

```python
def lift_vs_terrain(tpi, t, rows, cols, cut=0.4):
    """tpi, t: arrays 2D no mesmo grid; rows, cols: índices dos pontos de referência."""
    import numpy as np
    valid = np.isfinite(tpi) & np.isfinite(t)
    flag = (tpi > cut) & valid
    k = int(flag.sum())
    if k == 0:
        return {"lift": None, "lift_terrain": None, "ratio": None}
    share = k / valid.sum()
    hit = flag[rows, cols].mean()
    # base de terreno: os k pixels de maior T; empates quebrados por ordem estável
    order = np.argsort(-np.where(valid, t, -np.inf), axis=None, kind="stable")[:k]
    tflag = np.zeros(t.size, bool); tflag[order] = True; tflag = tflag.reshape(t.shape)
    thit = tflag[rows, cols].mean()
    return {"lift": hit / share, "lift_terrain": thit / share,
            "ratio": (hit / thit) if thit > 0 else None}
```

**Critério de aceite:** `metrics.json` publica `slide_warned_gfs_24h_lift_vs_terrain` e o equivalente para enchente contra o EMS. O README explica a diferença entre as duas bases.

### M2. Separar chuva de neve

**Opção 1, preferida:** usar a probabilidade de fase líquida do próprio IMERG V07 e somar só a precipitação com fase líquida provável. Confirmar o nome da banda no catálogo antes de rodar.

```javascript
// GEE: chuva líquida de 3 dias
var imerg = ee.ImageCollection('NASA/GPM_L3/IMERG_V07')
  .filterDate(t.advance(-3, 'day'), t)
  .map(function (img) {
    var liquid = img.select('probabilityLiquidPrecipitation').gte(50);
    return img.select('precipitation').updateMask(liquid).unmask(0);
  });
var rain3 = imerg.sum().multiply(0.5);  // mm
```

```python
# Now e replay local (GES DISC, HDF5 do IMERG V07)
liquid = prob_liquid >= 50          # variável probabilityLiquidPrecipitation, em %
rain_hh = np.where(liquid, precip, 0.0)
```

**Opção 2, complementar:** zerar T_slide onde houver neve no chão, com o MODIS Snow Cover (`MODIS/061/MOD10A1`, `NDSI_Snow_Cover >= 40`), usando o último dia sem nuvem. Em rios atmosféricos a nuvem limita essa opção.

**A climatologia precisa da mesma regra.** Se R usar só chuva líquida, os percentis também precisam ser recalculados só com chuva líquida, senão R fica subestimado.

**Critério de aceite:** em WA 2025, a área Crítica acima de 1.000 m cai de forma substancial, e o README publica a área antes e depois.

### M3. R além do P99

Proposta: estender a escala até 2× o P99, para manter gradação em eventos extremos.

```latex
R = \begin{cases}
0 & P_3 < P_{90} \\
0.40 \, \dfrac{P_3 - P_{90}}{P_{95} - P_{90}} & P_{90} \le P_3 < P_{95} \\
0.40 + 0.35 \, \dfrac{P_3 - P_{95}}{P_{99} - P_{95}} & P_{95} \le P_3 < P_{99} \\
0.75 + 0.25 \, \operatorname{clip}\!\left(\dfrac{P_3 - P_{99}}{P_{99}}\right) & P_3 \ge P_{99}
\end{cases}
```

Alternativa mais robusta: ajustar uma GEV aos máximos anuais de 3 dias por pixel e usar o período de retorno (por exemplo, 2 anos → 0 e 50 anos → 1). Isso resolve também o P99 apoiado em cerca de 5 amostras.

**Critério de aceite:** em RS 2024, o R médio da AOI no pico fica abaixo de 1 e varia entre pixels. M3 muda a escala do TPI, então só entra junto com M4.

### M4. Recalibrar os cortes de classe

Com a raiz cúbica, os cortes 0,2, 0,4 e 0,6 correspondem a produtos R·S·T de 0,008, 0,064 e 0,216. São permissivos: com chuva e solo no máximo, 18° de declividade já é Crítico.

Procedimento sugerido, sem vazar avaliação:

1. Calibrar em um caso e avaliar no outro (por exemplo, calibrar em WA e avaliar em RS, e vice-versa), ou nos eventos de VAL do ML.
2. Critério de calibração: a classe Crítica deve ter **ratio de M1 > 1** (acrescentar algo ao terreno) e área sinalizada compatível com a capacidade de resposta (por exemplo, até 2% da AOI).
3. Alternativa de interpretação mais simples: mudar a rampa de T_slide de 15°–30° para 20°–35°, ou aplicar os cortes ao produto R·S·T em vez da média geométrica.

**Critério de aceite:** tabela com os cortes antigos e novos, área sinalizada, acerto, lift e ratio de M1 nos dois casos.

### M5. HAND e planície costeira

1. Mudar `hand_proxy_radius_m` de 1500 para **1000**, o melhor raio validado (IoU 0,42 contra 0,34 em RS; 0,26 contra 0,19 em WA).
2. Na planície costeira, o proxy de raio não funciona. Opções:
   - HAND real por roteamento de fluxo no NASADEM (`pysheds` ou WhiteboxTools), mantendo só dado NASA;
   - mascarar áreas úmidas permanentes além da água permanente (por exemplo, JRC GSW ocorrência entre 10% e 50% como "banhado"), com rótulo próprio no mapa.

**Critério de aceite:** a mancha Crítica contínua a leste da Lagoa dos Patos some ou passa a seguir canais.

### M6. Corte duro de S

`soil_wetness_range = [0.5, 0.9]` zera o TPI em solos que nunca passam de 0,5, como em Washington no início de dezembro. Opções: rampa a partir de 0,3, ou S como anomalia em relação à climatologia do próprio SMAP no pixel, como R já faz com a chuva.

**Critério de aceite:** distribuição de S por caso publicada, e nenhum caso com mais de 50% da AOI em S = 0 durante a janela do evento sem justificativa.

---

## Anexo: como reproduzir os números desta revisão

Rodar na raiz do repositório, com `rasterio` e `numpy` instalados.

### 1. Áreas diárias e média de R e S (RS 2024)

```python
import rasterio, numpy as np
B = 'data/rs_2024/cog/rs_2024_'
ts = rasterio.open(B + 'tpi_slide_20240502.tif'); tf = rasterio.open(B + 'tpi_flood_20240502.tif')
R = rasterio.open(B + 'r_series_20240502.tif'); S = rasterio.open(B + 's_series_20240502.tif')
lat = np.array(ts.xy(np.arange(ts.height), np.zeros(ts.height, int))[1])
px = (ts.res[0] * 111.32 * np.cos(np.radians(lat))) * (ts.res[1] * 110.57)
A = np.repeat(px[:, None], ts.width, 1)
for i, d in enumerate(ts.descriptions):
    a, b = ts.read(i + 1), tf.read(i + 1)
    r, s = R.read(i + 1), S.read(i + 1)
    r[~np.isfinite(r)] = np.nan; s[~np.isfinite(s)] = np.nan
    print(d, 'slide crit km2', round(A[a > 0.6].sum(), 1), 'flood crit km2', round(A[b > 0.6].sum(), 1),
          'R mean', round(np.nanmean(r), 3), 'S mean', round(np.nanmean(s), 3))
```

### 2. Coerência do TPI com R, S e T publicados

Reamostrar R e S (1 km) para o grid de 90 m com bilinear, calcular `cbrt(R·S·T)` e comparar classes com o TPI publicado. Resultado: 98,6% (28/04), 98,9% (29/04), 99,4% (30/04) e 100% (02/05).

### 3. TPI contra base de terreno (A1 e A2)

```python
import rasterio, numpy as np, json
B = 'data/rs_2024/cog/rs_2024_'
gj = json.load(open('data/rs_2024/geojson/landslide_catalog.geojson'))
pts = np.array([c[:2] for f in gj['features'] for c in f['geometry']['coordinates']])
Td = rasterio.open(B + 't_slide_20240502.tif'); T = Td.read(1)
rows, cols = map(np.array, rasterio.transform.rowcol(Td.transform, pts[:, 0], pts[:, 1]))
g = rasterio.open(B + 'tpi_gfs_20240428.tif')
tpi = g.read(list(g.descriptions).index('d1_slide') + 1)
print(lift_vs_terrain(tpi, T, rows, cols))   # função da seção M1
# esperado: lift ~2,7 contra lift de terreno ~18,7
```

### 4. Confirmar o achado de neve em Washington com altitude real

```javascript
// GEE: altitude das células Críticas de deslizamento em 10/12/2025
var tpi = ee.Image('projects/SEU_PROJETO/assets/wa_2025_tpi_slide_20251210').select('20251210');
var dem = ee.Image('NASA/NASADEM_HGT/001').select('elevation');
var crit = tpi.gt(0.6);
var area = ee.Image.pixelArea().divide(1e6);
var bands = [[0, 500], [500, 1000], [1000, 1500], [1500, 5000]];
bands.forEach(function (b) {
  var m = crit.and(dem.gte(b[0])).and(dem.lt(b[1]));
  print('Crítico entre ' + b[0] + ' e ' + b[1] + ' m (km2)',
        area.updateMask(m).reduceRegion({reducer: ee.Reducer.sum(),
          geometry: tpi.geometry(), scale: 90, maxPixels: 1e10}));
});
```

Se mais da metade da área Crítica estiver acima de 1.000 m, o achado A3 se confirma.
