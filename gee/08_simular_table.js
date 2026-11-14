// ===== Pré-calcula os alertas do /simular para o caso RS 2024 =====
// Doc do projeto v3, "Código GEE 5". Lê o asset que 01_tpi_index.js exporta com
// Export.image.toAsset — sem ele este script não roda. Saída: CSV no Drive que
// tools/build_simular.py converte em simular.json (banda 0 = 1º dia da janela,
// 10 dias antes do evento).
var series = ee.Image('projects/<GEE_PROJECT>/assets/tipping-point/rs_2024_tpi_slide_series');

// Cidades da demo. Confiram as coordenadas antes de rodar.
var towns = ee.FeatureCollection([
  ee.Feature(ee.Geometry.Point([-51.87, -29.24]), {name: 'Encantado'}),
  ee.Feature(ee.Geometry.Point([-51.87, -29.16]), {name: 'Muçum'}),
  ee.Feature(ee.Geometry.Point([-51.87, -29.28]), {name: 'Roca Sales'}),
  ee.Feature(ee.Geometry.Point([-51.96, -29.47]), {name: 'Lajeado'}),
  ee.Feature(ee.Geometry.Point([-51.18, -29.92]), {name: 'Canoas'}),
  ee.Feature(ee.Geometry.Point([-51.23, -30.03]), {name: 'Porto Alegre'})
]);

// Maior TPI num raio de 2 km de cada cidade, para cada dia (uma propriedade por banda)
var table = series.reduceRegions({
  collection: towns.map(function (f) { return f.buffer(2000); }),
  reducer: ee.Reducer.max(),
  scale: 1000
});

print('Confira os valores antes de exportar', table);
Export.table.toDrive({
  collection: table, description: 'simular_rs_2024', fileFormat: 'CSV'});
