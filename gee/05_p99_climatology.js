// Reference mirror only — the canonical implementation is the Python gee/*.py
// modules, which read config.yaml. This script is not used by any pipeline.
// ===== P99 de chuva diária, ativo único =====
// Denominador do feature rain_rel = rain_24h / P99 (ml-predict §5.1, correção
// climática do LHASA). Pesado (~8400 imagens diárias): rode UMA vez, exporte
// como asset, e referencie o asset em 06_ml_features.js.
// Event: ../events/rs-2024-05.json (keep bbox in sync by hand)
var aoi = ee.Geometry.Rectangle([-52.5, -30.5, -50.5, -29.0]);

var imerg = ee.ImageCollection('NASA/GPM_L3/IMERG_V07')
  .select('precipitation').filterBounds(aoi);

// Série 2001-2023, um acumulado diário por imagem (IMERG é mm/h a cada 30 min)
var start = ee.Date('2001-01-01');
var nDays = ee.Date('2024-01-01').difference(start, 'day');
var daily = ee.ImageCollection(ee.List.sequence(0, nDays.subtract(1)).map(function (i) {
  var t0 = start.advance(i, 'day');
  return imerg.filterDate(t0, t0.advance(1, 'day')).sum().multiply(0.5)
    .rename('rain_mm');
}));

// P99 da chuva diária por pixel (não sazonal — §5.1 quer a série inteira)
var p99 = daily.reduce(ee.Reducer.percentile([99])).rename('p99_mm_day').clip(aoi);

Map.addLayer(p99, {min: 0, max: 150, palette: ['ffffb2', 'fd8d3c', 'bd0026']}, 'P99 chuva diária');

// Exporta como asset — é o que 06_ml_features.js lê
Export.image.toAsset({
  image: p99.toFloat(),
  description: 'p99_daily_rain_2001_2023',
  assetId: 'projects/<GEE_PROJECT>/assets/tipping-point/p99_daily_rain_2001_2023',
  region: aoi, scale: 11132 // ~0.1°, grade nativa do IMERG
});

// Para outro evento (ex.: WA), repita com o AOI e assetId do caso.
