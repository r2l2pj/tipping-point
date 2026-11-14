// ===== Suscetibilidade com Random Forest, nível 3a =====
// Draft from project doc. Catalog asset: projects/<GEE_PROJECT>/assets/tipping-point/glc_brasil
var region = ee.Geometry.Rectangle([-58, -34, -39, -19]); // Sul e Sudeste

var dem = ee.Image('NASA/NASADEM_HGT/001').select('elevation');
var terrain = ee.Terrain.products(dem).select(['elevation', 'slope', 'aspect']);
var curv = dem.convolve(ee.Kernel.laplacian8()).rename('curvature');
var lc = ee.ImageCollection('MODIS/061/MCD12Q1').filterDate('2023-01-01', '2024-01-01')
  .first().select('LC_Type1').rename('landcover');
var rain = ee.ImageCollection('NASA/GPM_L3/IMERG_MONTHLY_V07')
  .filterDate('2001-01-01', '2025-01-01').select('precipitation').mean().rename('rain_mean');
var soil = ee.ImageCollection('NASA/SMAP/SPL4SMGP/007')
  .filterDate('2016-01-01', '2025-01-01').select('sm_rootzone_wetness').mean().rename('soil_mean');
var stack = terrain.addBands([curv, lc, rain, soil]).clip(region);

var slides = ee.FeatureCollection('projects/GEE_PROJECT/assets/tipping-point/glc_brasil')
  .map(function (f) { return f.set('label', 1); });
var negs = ee.FeatureCollection.randomPoints(region, 3000, 42)
  .filter(ee.Filter.bounds(slides.geometry().buffer(5000)).not())
  .map(function (f) { return f.set('label', 0); });

var samples = stack.sampleRegions({
  collection: slides.merge(negs), properties: ['label'], scale: 90, geometries: true
}).randomColumn('r', 7);
// Para a versão final: separar treino e teste por região, não ao acaso
var train = samples.filter(ee.Filter.lt('r', 0.7));
var test = samples.filter(ee.Filter.gte('r', 0.7));

var rf = ee.Classifier.smileRandomForest(200).setOutputMode('PROBABILITY')
  .train(train, 'label', stack.bandNames());
var T_ml = stack.classify(rf).rename('T_ml');

print('Importância das variáveis', rf.explain().get('importance'));
var scored = test.classify(rf, 'prob');
print('Amostra de teste', scored.limit(10));
Map.addLayer(T_ml, {min: 0, max: 1, palette: ['ffffb2', 'fd8d3c', 'bd0026']}, 'Suscetibilidade ML');
