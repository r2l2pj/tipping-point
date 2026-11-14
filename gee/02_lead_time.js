// ===== Antecipação do alerta, nível 1 =====
// Depends on `series` from 01_tpi_index.js — paste both or reference the exported asset.
// Catalog asset: projects/<GEE_PROJECT>/assets/tipping-point/rs2024_initiation
// (UFRGS initiation points uploaded by ml/labels.py — the GLC has ZERO RS-2024
// rows, ml-predict §2.1, so it cannot validate this event.)
var firstCritical = series.map(function (img) {
  return ee.Image.constant(img.date().millis()).toDouble()
    .updateMask(img.select('TPI_slide').gt(0.6)).rename('t_crit');
}).min();

var slides = ee.FeatureCollection('projects/<GEE_PROJECT>/assets/tipping-point/rs2024_initiation');
// GLC fallback (pre-2017 events only, see note above):
// var slides = ee.FeatureCollection('projects/<GEE_PROJECT>/assets/tipping-point/glc_rs_2024');
var lead = firstCritical.sampleRegions({collection: slides, scale: 1000})
  .map(function (f) {
    // t_utc = tempo de pico-24h ATRIBUÍDO pelo labels.py (§2.2). Medir a
    // antecipação contra ele é circular/otimista — para a alegação pública use
    // um timestamp independente (relato datado da Defesa Civil/imprensa, §2.2).
    var hours = ee.Date(f.get('t_utc')).millis().subtract(f.get('t_crit')).divide(3600000);
    // GLC version: ee.Number(f.get('event_date')).subtract(f.get('t_crit')).divide(3600000)
    return f.set('lead_hours', hours);
  });
print('Antecipação média (h)', lead.aggregate_mean('lead_hours'));
print('Deslizamentos avisados antes', lead.filter(ee.Filter.gt('lead_hours', 0)).size(), 'de', slides.size());
