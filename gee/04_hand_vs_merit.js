// ===== Validação do proxy de HAND contra o MERIT Hydro =====
// MERIT é REFERÊNCIA de validação apenas — nunca entra no índice (regra NASA-only).
var aoi = ee.Geometry.Rectangle([-52.5, -30.5, -50.5, -29.0]); // ../events/rs-2024-05.json
var dem = ee.Image('NASA/NASADEM_HGT/001').select('elevation');
var merit = ee.Image('MERIT/Hydro/v1_0_1').select('hnd').rename('merit');

function validate(radius) {
  var proxy = dem.subtract(dem.focalMin({radius: radius, units: 'meters'})).rename('proxy');
  var pair = proxy.addBands(merit).clip(aoi);

  // 1. Correlação de Pearson numa amostra
  var sample = pair.sample({region: aoi, scale: 90, numPixels: 5000, seed: 1});
  var corr = sample.reduceColumns(ee.Reducer.pearsonsCorrelation(), ['proxy', 'merit']);

  // 2. IoU da zona de risco (HAND abaixo de 5 m)
  var lowProxy = proxy.lt(5), lowMerit = merit.lt(5);
  var stats = lowProxy.and(lowMerit).rename('both')
    .addBands(lowProxy.or(lowMerit).rename('any'))
    .reduceRegion({reducer: ee.Reducer.sum(), geometry: aoi, scale: 90, maxPixels: 1e9});
  var iou = ee.Number(stats.get('both')).divide(stats.get('any'));

  return ee.Feature(null, {radius_m: radius, r: corr.get('correlation'), iou_low_hand: iou});
}

// Testa três raios e escolhe o que mais concorda com o MERIT
var results = ee.FeatureCollection([validate(1000), validate(1500), validate(3000)]);
print('Proxy x MERIT por raio', results);

// Mapa de diferença para o README
var proxy15 = dem.subtract(dem.focalMin({radius: 1500, units: 'meters'}));
Map.addLayer(proxy15.subtract(merit).clip(aoi),
  {min: -10, max: 10, palette: ['2166ac', 'f7f7f7', 'b2182b']}, 'Proxy menos MERIT (m)');
