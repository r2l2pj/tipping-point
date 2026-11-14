// ===== Tipping Point Index, Modo 1 — variante WA 2025 =====
// Mesma lógica de 01_tpi_index.js, caso Washington.
// Event: ../events/wa-2025-12.json (keep bbox in sync by hand)
var aoi = ee.Geometry.Rectangle([-122.6, 47.0, -121.5, 48.5]);
var ref = ee.Date('2025-12-10'); // data de referência do evento

var imerg = ee.ImageCollection('NASA/GPM_L3/IMERG_V07').select('precipitation');
var smap = ee.ImageCollection('NASA/SMAP/SPL4SMGP/008').select('sm_rootzone_wetness');
var dem = ee.Image('NASA/NASADEM_HGT/001').select('elevation');
var slope = ee.Terrain.slope(dem);

// Chuva acumulada em N dias (IMERG é mm/h a cada 30 min)
function accum(end, days) {
  return imerg.filterDate(end.advance(-days, 'day'), end).sum().multiply(0.5);
}
// Umidade do solo: média das 24 h anteriores
function smapMean(end) {
  return smap.filterDate(end.advance(-1, 'day'), end).mean();
}

// Climatologia: janelas de 3 dias na mesma época do ano, 2001 a 2023.
// Pesado: exporta UMA vez como asset (rode com CLIM_ASSET_EXISTS=false) e
// depois marque true — o asset é reutilizado em todas as próximas corridas.
var CLIM_ASSET = 'projects/<GEE_PROJECT>/assets/tipping-point/wa_2025_clim_rain3_2001_2023';
var CLIM_ASSET_EXISTS = false;
var pct;
if (CLIM_ASSET_EXISTS) {
  pct = ee.Image(CLIM_ASSET);
} else {
  var years = ee.List.sequence(2001, 2023);
  var offsets = ee.List.sequence(-30, 30, 3);
  var clim = ee.ImageCollection.fromImages(years.map(function (y) {
    return offsets.map(function (o) {
      var end = ee.Date.fromYMD(y, ref.get('month'), ref.get('day')).advance(o, 'day');
      return accum(end, 3);
    });
  }).flatten());
  pct = clim.reduce(ee.Reducer.percentile([90, 95, 99]));
  Export.image.toAsset({
    image: pct.toFloat().clip(aoi),
    description: 'wa_2025_clim_rain3_2001_2023',
    assetId: CLIM_ASSET, region: aoi, scale: 10000
  });
}
var p90 = pct.select('precipitation_p90');
var p95 = pct.select('precipitation_p95');
var p99 = pct.select('precipitation_p99');

// Terreno
var T_slide = slope.unitScale(15, 30).clamp(0, 1);           // 0 abaixo de 15 graus, 1 acima de 30
var hand = dem.subtract(dem.focalMin({radius: 1500, units: 'meters'})); // proxy de HAND
var T_flood = ee.Image(1).subtract(hand.unitScale(5, 15)).clamp(0, 1)
  .where(slope.gt(5), 0);

// TPI = média geométrica ponderada R^wR · S^wS · T^wT (doc v4).
// Pesos de config.yaml thresholds.tpi_weights; uniformes = (R·S·T)^(1/3).
var TPI_W = [0.3333, 0.3333, 0.3334]; // [R, S, T]
function tpi(r, s, t) {
  return r.pow(TPI_W[0]).multiply(s.pow(TPI_W[1])).multiply(t.pow(TPI_W[2]));
}

function tpiAt(t) {
  t = ee.Date(t);
  var rain3 = accum(t, 3);
  var R = ee.Image(0)
    .where(rain3.gte(p90), rain3.subtract(p90).divide(p95.subtract(p90)).multiply(0.5))
    .where(rain3.gte(p95), rain3.subtract(p95).divide(p99.subtract(p95)).multiply(0.5).add(0.5))
    .where(rain3.gte(p99), 1).clamp(0, 1);
  var S = smapMean(t).unitScale(0.5, 0.9).clamp(0, 1);
  // R/S vêm junto sem classificar — os COGs r_series/s_series (leitura de
  // pixel do app, geotiff.js) selecionam essas bandas direto.
  return R.rename('R').addBands(S.rename('S'))
    .addBands(tpi(R, S, T_slide).rename('TPI_slide'))
    .addBands(tpi(R, S, T_flood).rename('TPI_flood'))
    .set('system:time_start', t.millis());
}

// Série diária: 10 dias antes até 10 depois do evento
var dates = ee.List.sequence(-10, 10).map(function (d) { return ref.advance(d, 'day'); });
// Nome da banda = data do dia (YYYYMMDD); 1ª banda = 1º dia da janela
var bandNames = dates.map(function (d) { return ee.Date(d).format('YYYYMMdd'); });
var series = ee.ImageCollection(dates.map(tpiAt));

// Classes: 0 Baixo, 1 Moderado, 2 Alto, 3 Crítico
function classes(img) { return img.gt(0.2).add(img.gt(0.4)).add(img.gt(0.6)); }
var pal = ['ffffb2', 'fecc5c', 'fd8d3c', 'bd0026'];
var peak = tpiAt(ref);
Map.centerObject(aoi, 8);
Map.addLayer(classes(peak.select('TPI_slide')).clip(aoi), {min: 0, max: 3, palette: pal}, 'TPI deslizamento');
Map.addLayer(classes(peak.select('TPI_flood')).clip(aoi), {min: 0, max: 3, palette: pal}, 'TPI enchente');

// Exporta as séries como GeoTIFFs multibanda (uma banda por dia, nome = data)
// para o app: data/wa_2025/cog/wa_2025_tpi_{slide,flood}_20251210.tif
Export.image.toDrive({
  image: series.select('TPI_slide').toBands().rename(bandNames).toFloat().clip(aoi),
  description: 'wa_2025_tpi_slide_20251210', region: aoi, scale: 1000,
  fileFormat: 'GeoTIFF', formatOptions: {cloudOptimized: true}
});
Export.image.toDrive({
  image: series.select('TPI_flood').toBands().rename(bandNames).toFloat().clip(aoi),
  description: 'wa_2025_tpi_flood_20251210', region: aoi, scale: 1000,
  fileFormat: 'GeoTIFF', formatOptions: {cloudOptimized: true}
});

// Notas R e S por dia + terreno estático — float32 SEM classificar, lidos
// pelo popup do app (geotiff.js): data/wa_2025/cog/wa_2025_{r,s}_series_*.tif
// e wa_2025_t_{slide,flood}_*.tif
Export.image.toDrive({
  image: series.select('R').toBands().rename(bandNames).toFloat().clip(aoi),
  description: 'wa_2025_r_series_20251210', region: aoi, scale: 1000,
  fileFormat: 'GeoTIFF', formatOptions: {cloudOptimized: true}
});
Export.image.toDrive({
  image: series.select('S').toBands().rename(bandNames).toFloat().clip(aoi),
  description: 'wa_2025_s_series_20251210', region: aoi, scale: 1000,
  fileFormat: 'GeoTIFF', formatOptions: {cloudOptimized: true}
});
Export.image.toDrive({
  image: T_slide.toFloat().clip(aoi),
  description: 'wa_2025_t_slide_20251210', region: aoi, scale: 1000,
  fileFormat: 'GeoTIFF', formatOptions: {cloudOptimized: true}
});
Export.image.toDrive({
  image: T_flood.toFloat().clip(aoi),
  description: 'wa_2025_t_flood_20251210', region: aoi, scale: 1000,
  fileFormat: 'GeoTIFF', formatOptions: {cloudOptimized: true}
});

// Assets derivados = fonte da verdade (L8): tudo sob
// projects/<GEE_PROJECT>/assets/tipping-point/wa_2025/. Bandas = YYYYMMDD
// ('20251130' = 1º dia da janela, '20251210' = dia do evento).
var CASE_ASSETS = 'projects/<GEE_PROJECT>/assets/tipping-point/wa_2025';
var derived = {
  tpi_slide_series: 'TPI_slide', tpi_flood_series: 'TPI_flood',
  r_series: 'R', s_series: 'S'};
Object.keys(derived).forEach(function (name) {
  Export.image.toAsset({
    image: series.select(derived[name]).toBands().rename(bandNames).toFloat().clip(aoi),
    description: 'wa_2025_' + name,
    assetId: CASE_ASSETS + '/' + name, region: aoi, scale: 1000});
});
Export.image.toAsset({
  image: T_slide.toFloat().clip(aoi), description: 'wa_2025_t_slide',
  assetId: CASE_ASSETS + '/t_slide', region: aoi, scale: 1000});
Export.image.toAsset({
  image: T_flood.toFloat().clip(aoi), description: 'wa_2025_t_flood',
  assetId: CASE_ASSETS + '/t_flood', region: aoi, scale: 1000});

// E TAMBÉM com os nomes antigos, no mesmo padrão do RS (gee/08 e gee/09
// leem os assets planos).
Export.image.toAsset({
  image: series.select('TPI_slide').toBands().rename(bandNames).toFloat().clip(aoi),
  description: 'tpi_slide_series_wa_asset',
  assetId: 'projects/<GEE_PROJECT>/assets/tipping-point/wa_2025_tpi_slide_series',
  region: aoi, scale: 1000
});
Export.image.toAsset({
  image: series.select('TPI_flood').toBands().rename(bandNames).toFloat().clip(aoi),
  description: 'tpi_flood_series_wa_asset',
  assetId: 'projects/<GEE_PROJECT>/assets/tipping-point/wa_2025_tpi_flood_series',
  region: aoi, scale: 1000
});

// Média de área por dia (rain3, rain7 e solo) → CSV que vira
// data/wa_2025/series/wa_2025_rain_soil_20251210.json
var rainSoil = ee.FeatureCollection(dates.map(function (d) {
  d = ee.Date(d);
  var img = accum(d, 3).rename('rain3_mm')
    .addBands(accum(d, 7).rename('rain7_mm'))
    .addBands(smapMean(d).rename('smap'));
  var stats = img.reduceRegion({
    reducer: ee.Reducer.mean(), geometry: aoi, scale: 1000, maxPixels: 1e9});
  return ee.Feature(null, stats.set('date', d.format('YYYY-MM-dd')));
}));
Export.table.toDrive({
  collection: rainSoil, description: 'wa_2025_rain_soil_20251210',
  fileFormat: 'CSV'});
