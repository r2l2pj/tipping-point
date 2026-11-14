// ===== Tipping Point Index, Modo 1 =====
// Draft from project doc. Event: ../events/rs-2024-05.json (keep bbox in sync by hand)
var aoi = ee.Geometry.Rectangle([-52.5, -30.5, -50.5, -29.0]);
var ref = ee.Date('2024-05-02'); // data de referência do evento

var imerg = ee.ImageCollection('NASA/GPM_L3/IMERG_V07').select('precipitation');
var dem = ee.Image('NASA/NASADEM_HGT/001').select('elevation');
var slope = ee.Terrain.slope(dem);

// Chuva acumulada em N dias (IMERG é mm/h a cada 30 min)
function accum(end, days) {
  return imerg.filterDate(end.advance(-days, 'day'), end).sum().multiply(0.5);
}

// Climatologia: janelas de 3 dias na mesma época do ano, 2001 a 2023
// Pesado: rode uma vez e exporte como asset
var years = ee.List.sequence(2001, 2023);
var offsets = ee.List.sequence(-30, 30, 3);
var clim = ee.ImageCollection.fromImages(years.map(function (y) {
  return offsets.map(function (o) {
    var end = ee.Date.fromYMD(y, ref.get('month'), ref.get('day')).advance(o, 'day');
    return accum(end, 3);
  });
}).flatten());
var pct = clim.reduce(ee.Reducer.percentile([90, 95, 99]));
var p90 = pct.select('precipitation_p90');
var p95 = pct.select('precipitation_p95');
var p99 = pct.select('precipitation_p99');

// Terreno
var T_slide = slope.unitScale(15, 30).clamp(0, 1);           // 0 abaixo de 15 graus, 1 acima de 30
var hand = dem.subtract(dem.focalMin({radius: 1500, units: 'meters'})); // proxy de HAND
var T_flood = ee.Image(1).subtract(hand.unitScale(5, 15)).clamp(0, 1)
  .where(slope.gt(5), 0);

function tpiAt(t) {
  t = ee.Date(t);
  var rain3 = accum(t, 3);
  var R = ee.Image(0)
    .where(rain3.gte(p90), rain3.subtract(p90).divide(p95.subtract(p90)).multiply(0.5))
    .where(rain3.gte(p95), rain3.subtract(p95).divide(p99.subtract(p95)).multiply(0.5).add(0.5))
    .where(rain3.gte(p99), 1).clamp(0, 1);
  var S = ee.ImageCollection('NASA/SMAP/SPL4SMGP/008').select('sm_rootzone_wetness')
    .filterDate(t.advance(-1, 'day'), t).mean().unitScale(0.5, 0.9).clamp(0, 1);
  return R.multiply(S).multiply(T_slide).rename('TPI_slide')
    .addBands(R.multiply(S).multiply(T_flood).rename('TPI_flood'))
    .set('system:time_start', t.millis());
}

// Série diária: 10 dias antes até 5 depois do evento
var dates = ee.List.sequence(-10, 5).map(function (d) { return ref.advance(d, 'day'); });
var series = ee.ImageCollection(dates.map(tpiAt));

// Classes: 0 Baixo, 1 Moderado, 2 Alto, 3 Crítico
function classes(img) { return img.gt(0.2).add(img.gt(0.4)).add(img.gt(0.6)); }
var pal = ['ffffb2', 'fecc5c', 'fd8d3c', 'bd0026'];
var peak = tpiAt(ref);
Map.centerObject(aoi, 8);
Map.addLayer(classes(peak.select('TPI_slide')).clip(aoi), {min: 0, max: 3, palette: pal}, 'TPI deslizamento');
Map.addLayer(classes(peak.select('TPI_flood')).clip(aoi), {min: 0, max: 3, palette: pal}, 'TPI enchente');

// Exporta a série como um GeoTIFF multibanda (uma banda por dia) para o app
Export.image.toDrive({
  image: series.select('TPI_slide').toBands().toFloat().clip(aoi),
  description: 'tpi_slide_series_rs', region: aoi, scale: 1000,
  fileFormat: 'GeoTIFF', formatOptions: {cloudOptimized: true}
});

// E TAMBÉM como asset — o gee/08_simular_table.js (Telegram /simular) lê ESTE
// asset, não o COG. As bandas viram 0_TPI_slide, 1_TPI_slide... (0 = 1º dia da
// janela, 10 dias antes do evento).
Export.image.toAsset({
  image: series.select('TPI_slide').toBands().toFloat().clip(aoi),
  description: 'tpi_slide_series_rs_asset',
  assetId: 'projects/<GEE_PROJECT>/assets/tipping-point/rs_2024_tpi_slide_series',
  region: aoi, scale: 1000
});
