// Reference mirror only — the canonical implementation is the Python gee/*.py
// modules, which read config.yaml. This script is not used by any pipeline.
// ===== Feature table do modelo de gatilho, nível 3b =====
// ml-predict §5.1 (colunas) e §2.3 (amostragem pos/N1/N2), replay do RS 2024.
// Dependências de asset (rode antes):
//   - 05_p99_climatology.js  → .../p99_daily_rain_2001_2023
//   - ml/labels.py           → .../rs2024_initiation (UFRGS, um ponto por célula
//     de 90 m, com propriedade t_utc = fim da janela de 24 h de pico, ISO-8601;
//     data atribuída, §2.2)
// Saídas (arraste para data/<event>/cog|raw depois):
//   1) static stack COG @90m   2) dynamic stack COG (~0.1°, 5 bandas × frame
//   diário)   3) features CSV ~50k linhas via Export.table.
// Event: ../events/rs-2024-05.json (keep bbox/dates in sync by hand)
var aoi = ee.Geometry.Rectangle([-52.5, -30.5, -50.5, -29.0]);
var winStart = ee.Date('2024-04-20'), winEnd = ee.Date('2024-05-31'); // window

var ASSETS = 'projects/<GEE_PROJECT>/assets/tipping-point';
var p99 = ee.Image(ASSETS + '/p99_daily_rain_2001_2023').select(0).rename('p99');
var slides = ee.FeatureCollection(ASSETS + '/rs2024_initiation');

var imerg = ee.ImageCollection('NASA/GPM_L3/IMERG_V07').select('precipitation');
var smap = ee.ImageCollection('NASA/SMAP/SPL4SMGP/008').select('sm_profile_wetness');
var dem = ee.Image('NASA/NASADEM_HGT/001').select('elevation');

// ---------- Stack estático @90 m (§5.1) ----------
function to90m(img, reducer) {
  return img.reduceResolution({reducer: reducer, maxPixels: 64})
    .reproject({crs: 'EPSG:4326', scale: 90});
}
var slope30 = ee.Terrain.slope(dem);
var aspectRad = ee.Terrain.aspect(dem).multiply(Math.PI / 180);
var staticStack = to90m(slope30, ee.Reducer.mean()).rename('slope_mean')
  .addBands(to90m(slope30, ee.Reducer.max()).rename('slope_max'))
  .addBands(to90m(
    dem.focalMax({radius: 500, units: 'meters'})
      .subtract(dem.focalMin({radius: 500, units: 'meters'})),
    ee.Reducer.mean()).rename('relief_500m'))
  .addBands(to90m(dem.convolve(ee.Kernel.laplacian8()), ee.Reducer.mean()).rename('curv'))
  .addBands(to90m(aspectRad.sin(), ee.Reducer.mean()).rename('aspect_sin'))
  .addBands(to90m(aspectRad.cos(), ee.Reducer.mean()).rename('aspect_cos'))
  .addBands(to90m(dem, ee.Reducer.mean()).rename('elev'))
  // mesmo proxy de HAND do índice (raio validado em 04_hand_vs_merit.js)
  .addBands(to90m(dem.subtract(dem.focalMin({radius: 1500, units: 'meters'})),
    ee.Reducer.mean()).rename('hand_proxy'))
  .addBands(to90m(
    ee.ImageCollection('MODIS/061/MCD12Q1').filterDate('2023-01-01', '2024-01-01')
      .first().select('LC_Type1'),
    ee.Reducer.mode()).rename('lc'));

// ---------- Features dinâmicos no instante t (§5.1) ----------
function accum(t0, t1) {
  return imerg.filterDate(t0, t1).sum().multiply(0.5);
}
function dynamicAt(t) {
  t = ee.Date(t);
  var rain24 = accum(t.advance(-24, 'hour'), t).rename('rain_24h');
  var ante2d = accum(t.advance(-72, 'hour'), t.advance(-24, 'hour')).rename('rain_ante_2d');
  var rain7d = accum(t.advance(-7, 'day'), t).rename('rain_7d');
  // denominador travado em >=1 mm: pixel que quase nunca chove não explode o ratio
  var rainRel = rain24.divide(p99.max(1)).rename('rain_rel');
  // honesto à latência: SMAP L4 chega com >2 d — usa a média de [t-4d, t-3d] (§3)
  var sm = smap.filterDate(t.advance(-4, 'day'), t.advance(-3, 'day')).mean()
    .rename('sm_profile_wetness');
  return rain24.addBands([ante2d, rain7d, rainRel, sm]);
}

// ---------- Pontos de treino (§2.3) ----------
// Positivos: pontos de iniciação UFRGS já deduplicados a 90 m pelo labels.py.
var pos = slides.map(function (f) {
  return f.set({label: 1, set: 'pos'});
});
var tList = ee.List(slides.aggregate_array('t_utc').distinct());
var nT = tList.size();

// Footprint mapeado: hull dos pontos como proxy — §2.3 manda checar o footprint
// real nos metadados do inventário; troque por um asset de footprint se labels.py
// exportar um.
var footprint = slides.geometry().convexHull(100).intersection(aoi, 100);
var near = slides.geometry().buffer(250); // >=250 m de qualquer iniciação

// N1 ~20k: dentro do footprint, longe dos polígonos, mesma janela de t dos
// positivos, metade com declividade >10° (negativos duros).
var n1pool = ee.FeatureCollection.randomPoints(footprint, 40000, 42)
  .filter(ee.Filter.bounds(near).not());
var n1sl = slope30.sampleRegions({collection: n1pool, scale: 30, tileScale: 4});
function tagN1(f) {
  var u = ee.Number(f.get('random'));
  return f.set({label: 0, set: 'N1',
    t_utc: tList.get(u.multiply(nT).floor().min(nT.subtract(1)))});
}
var n1 = n1sl.randomColumn('random', 7).filter(ee.Filter.gt('slope', 10))
  .sort('random').limit(10000).map(tagN1)
  .merge(n1sl.randomColumn('random', 8).sort('random').limit(10000)
    .map(tagN1)); // metade "anywhere" (declividade livre)

// N2 ~20k: células positivas + aleatórias em ~200 dias calmos,
// 2015-04-01 a 2024-03-31, fora das janelas de chuva forte conhecidas no RS
// (meses inteiros, conservador — datas exatas não verificadas, §2.3).
var n2Start = ee.Date('2015-04-01');
var nDays2 = ee.Date('2024-03-31').difference(n2Start, 'day');
var rainWindows = [['2023-06-01', '2023-07-01'], ['2023-09-01', '2023-10-01'],
                   ['2023-11-01', '2023-12-01']];
var calm = ee.FeatureCollection(ee.List.sequence(0, nDays2.subtract(1)).map(function (i) {
  return ee.Feature(null, {t: n2Start.advance(i, 'day').millis()});
}));
rainWindows.forEach(function (w) {
  calm = calm.filter(ee.Filter.or(
    ee.Filter.lt('t', ee.Date(w[0]).millis()),
    ee.Filter.gte('t', ee.Date(w[1]).millis())));
});
var n2days = calm.randomColumn('u', 11).sort('u').limit(200).aggregate_array('t');
var posFrac = ee.Number(60).divide(slides.size()); // ~60 positivos por dia
var n2 = ee.FeatureCollection(n2days.map(function (m) {
  m = ee.Number(m);
  var seed = m.mod(1e6).int();
  var dayPos = slides.randomColumn('u', seed).filter(ee.Filter.lt('u', posFrac));
  var dayRand = ee.FeatureCollection.randomPoints(aoi, 60, seed);
  return dayPos.merge(dayRand).map(function (f) {
    return f.set({label: 0, set: 'N2', t_utc: ee.Date(m).format('YYYY-MM-dd')});
  });
})).flatten();

// Colunas de identidade da §5.1 enquanto as geometrias ainda existem:
// lon, lat, block_id (blocos de 0.25° para o CV espacial em GroupKFold)
var samples = pos.merge(n1).merge(n2).map(function (f) {
  var c = f.geometry().coordinates();
  var lon = ee.Number(c.get(0)), lat = ee.Number(c.get(1));
  var bx = lon.divide(0.25).floor().format('%.0f');
  var by = lat.divide(0.25).floor().format('%.0f');
  return f.set({lon: lon, lat: lat, block_id: bx.cat('_').cat(by)});
});
print('Linhas por conjunto (rode antes de exportar)', samples.aggregate_histogram('set'));

// ---------- Extração: estático + dinâmico no t de cada linha ----------
// Agrupa por t_utc (~10 valores nos positivos/N1 + ~200 nos N2) e amostra o
// stack de cada data.
var tValues = samples.aggregate_array('t_utc').distinct();
var table = ee.FeatureCollection(tValues.map(function (tv) {
  var img = staticStack.addBands(dynamicAt(ee.Date(tv)));
  var pts = samples.filter(ee.Filter.eq('t_utc', tv));
  return img.sampleRegions({
    collection: pts, scale: 90, tileScale: 4, geometries: false});
})).flatten();

// ---------- Exportações ----------
// 1) Features CSV (entrada de ml/dataset.py)
Export.table.toDrive({
  collection: table, description: 'rs_2024_ml_features', fileFormat: 'CSV'});

// 2) Static stack COG (predict.py usa como grade do produto)
Export.image.toDrive({
  image: staticStack.toFloat().clip(aoi),
  description: 'rs_2024_static_stack', region: aoi,
  crs: 'EPSG:4326', scale: 90,
  fileFormat: 'GeoTIFF', formatOptions: {cloudOptimized: true}});

// 3) Dynamic stack COG: 5 bandas por frame diário da janela do caso
// (bandas 0_rain_24h, 0_rain_ante_2d, ... — o índice é o dia desde winStart)
var nFrames = winEnd.difference(winStart, 'day');
var dynSeries = ee.ImageCollection(ee.List.sequence(0, nFrames).map(function (i) {
  var t = winStart.advance(i, 'day');
  return dynamicAt(t).set('system:time_start', t.millis());
}));
Export.image.toDrive({
  image: dynSeries.toBands().toFloat(),
  description: 'rs_2024_dynamic_stack', region: aoi,
  crs: 'EPSG:4326', scale: 11132, // ~0.1° nativo IMERG; SMAP (~9 km) reamostrado
  fileFormat: 'GeoTIFF', formatOptions: {cloudOptimized: true}});
