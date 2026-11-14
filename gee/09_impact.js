// ===== Camada de impacto (doc v4 "impact") =====
// Run AFTER 01_tpi_index.js / 07_tpi_wa.js (they export the TPI series assets
// this script reads). Events: ../events/rs-2024-05.json, wa-2025-12.json.
//
// For each case:
//   people_critical      GPWv4 population sum inside TPI_slide > 0.6 on the event day
//   people_flooded       GPWv4 population sum inside the NISAR flood mask (wa only)
//   flooded_km2          km2 of the NISAR flood mask (wa only)
//   night_light_drop_pct VIIRS Black Marble: mean radiance of the 7 nights
//                        after the event vs the 30 nights before, in the
//                        affected area (critical ∪ flood)
// Output: printed ee.Dictionary + a 1-row CSV export per case; merge the keys
// into data/<case>/metrics.json (see data/SCHEMA.md).

var TPI_SLIDE_ASSET = {
  rs_2024: 'projects/<GEE_PROJECT>/assets/tipping-point/rs_2024_tpi_slide_series',
  wa_2025: 'projects/<GEE_PROJECT>/assets/tipping-point/wa_2025_tpi_slide_series'
};
// Band 0 = first window day; the event day is band <window_before_days> = 10
// for both cases (see events/*.json gee.window_*_days).
var EVENT_BAND = {rs_2024: 10, wa_2025: 10};

// PREREQUISITE for people_flooded/flooded_km2: upload the NISAR flood mask
// (data/wa-2025-12/cog/flood_nisar.tif, from pipeline/scripts/02_flood_mask.py):
//   earthengine upload image --asset_id \
//     projects/<GEE_PROJECT>/assets/tipping-point/wa_2025_flood_nisar flood_nisar.tif
// Until then keep HAS_NISAR_FLOOD=false — the two keys come out null.
var HAS_NISAR_FLOOD = false;
var WA_FLOOD_ASSET = 'projects/<GEE_PROJECT>/assets/tipping-point/wa_2025_flood_nisar';

var GPW = 'CIESIN/GPWv411/GPW_Population_Count';   // epochs every 5 yr, latest 2020
var NTL = 'NASA/VIIRS/002/VNP46A2';                 // DNB_BRDF_Corrected_NTL
var CRITICAL = 0.6;                                // tpi_classes cut for Crítico

function gpwNearest(ref) {
  // GPW epoch closest to the event year; gpw_year goes into metrics.json.
  return ee.Image(ee.ImageCollection(GPW).map(function (img) {
    return img.set('d', img.date().difference(ref, 'year').abs());
  }).sort('d').first());
}

function nightLights(ref, affected, aoi) {
  var ntl = ee.ImageCollection(NTL)
    .select(['DNB_BRDF_Corrected_NTL', 'Mandatory_Quality_Flag'])
    .map(function (img) {
      return img.select('DNB_BRDF_Corrected_NTL')
        .updateMask(img.select('Mandatory_Quality_Flag').eq(0));
    });
  var scale = 500; // VNP46A2 is ~15 arc-second
  var before = ntl.filterDate(ref.advance(-30, 'day'), ref).mean()
    .updateMask(affected)
    .reduceRegion({reducer: ee.Reducer.mean(), geometry: aoi,
                   scale: scale, maxPixels: 1e9})
    .get('DNB_BRDF_Corrected_NTL');
  var after = ntl.filterDate(ref, ref.advance(7, 'day')).mean()
    .updateMask(affected)
    .reduceRegion({reducer: ee.Reducer.mean(), geometry: aoi,
                   scale: scale, maxPixels: 1e9})
    .get('DNB_BRDF_Corrected_NTL');
  return ee.Number(before).subtract(ee.Number(after))
    .divide(ee.Number(before)).multiply(100);
}

function impact(caseId, aoi, eventDate, bandIndex, floodMask) {
  var ref = ee.Date(eventDate);
  var tpiDay = ee.Image(TPI_SLIDE_ASSET[caseId]).select(bandIndex).rename('TPI_slide');
  var critical = tpiDay.gt(CRITICAL).selfMask();
  var scale = 1000; // TPI series exported at 1 km; GPW is ~30 arc-second

  var pop = gpwNearest(ref).select(0); // band 'population_count'
  var gpwYear = gpwNearest(ref).date().get('year');
  var sumRegion = function (mask) {
    return pop.updateMask(mask).reduceRegion({
      reducer: ee.Reducer.sum(), geometry: aoi, scale: scale, maxPixels: 1e9
    }).get('population_count');
  };

  var metrics = {
    case: caseId,
    event_date: eventDate,
    gpw_year: gpwYear,
    people_critical: sumRegion(critical),
    people_flooded: null,
    flooded_km2: null,
    night_light_drop_pct: nightLights(ref, critical, aoi)
  };

  if (floodMask) {
    var affected = critical.unmask().add(floodMask.unmask()).gt(0).selfMask();
    metrics.people_flooded = sumRegion(floodMask);
    metrics.flooded_km2 = ee.Image.pixelArea().updateMask(floodMask)
      .reduceRegion({reducer: ee.Reducer.sum(), geometry: aoi,
                     scale: scale, maxPixels: 1e9})
      .getNumber('area').divide(1e6);
    metrics.night_light_drop_pct = nightLights(ref, affected, aoi);
  }

  // plain object: ee.Dictionary cannot hold the nulls used for absent keys
  print('impact ' + caseId, metrics);
  Map.addLayer(critical.clip(aoi), {palette: ['bd0026']}, caseId + ' critico');
  Export.table.toDrive({
    collection: ee.FeatureCollection([ee.Feature(null, metrics)]),
    description: 'impact_' + caseId, fileFormat: 'CSV'
  });
  return metrics;
}

impact('rs_2024', ee.Geometry.Rectangle([-52.5, -30.5, -50.5, -29.0]),
       '2024-05-02', EVENT_BAND.rs_2024, null);
impact('wa_2025', ee.Geometry.Rectangle([-122.6, 47.0, -121.5, 48.5]),
       '2025-12-10', EVENT_BAND.wa_2025,
       HAS_NISAR_FLOOD ? ee.Image(WA_FLOOD_ASSET).select(0).gt(0) : null);
