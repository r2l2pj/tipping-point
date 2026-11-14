// ===== Antecipação do alerta, nível 1 =====
// Depends on `series` from 01_tpi_index.js — paste both or reference the exported asset.
// Catalog points asset: projects/<GEE_PROJECT>/assets/tipping-point/glc_rs_2024
var firstCritical = series.map(function (img) {
  return ee.Image.constant(img.date().millis()).toDouble()
    .updateMask(img.select('TPI_slide').gt(0.6)).rename('t_crit');
}).min();

// Deslizamentos do catálogo com a data do evento (campo event_date em milissegundos)
var slides = ee.FeatureCollection('projects/GEE_PROJECT/assets/tipping-point/glc_rs_2024');
var lead = firstCritical.sampleRegions({collection: slides, scale: 1000})
  .map(function (f) {
    var hours = ee.Number(f.get('event_date')).subtract(f.get('t_crit')).divide(3600000);
    return f.set('lead_hours', hours);
  });
print('Antecipação média (h)', lead.aggregate_mean('lead_hours'));
print('Deslizamentos avisados antes', lead.filter(ee.Filter.gt('lead_hours', 0)).size(), 'de', slides.size());
