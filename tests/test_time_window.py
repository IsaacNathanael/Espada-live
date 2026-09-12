from pathlib import Path
import json
import pandas as pd
import shapefile
from espada.environment import synthetic_environment
from espada.historical_case import build_blinded_candidates, build_unosat_slick
from espada.time_window import search_release_window

def test_release_window_search_does_not_need_truth(tmp_path: Path) -> None:
    shp = tmp_path / "oil.shp"; writer = shapefile.Writer(str(shp)); writer.field("SensorID","C"); writer.field("Confidence","C"); writer.field("Area_m2","N",decimal=1); writer.field("Field_Vali","C")
    writer.poly([[[57.73,-20.44],[57.75,-20.44],[57.75,-20.42],[57.73,-20.44]]]); writer.record("Sentinel-2","High",231436.0,"Not validated"); writer.close()
    slick = tmp_path / "slick.geojson"; build_unosat_slick(shp, slick)
    gfw = tmp_path / "gfw.csv"; pd.DataFrame({"timestamp_utc":["2020-08-05T00:00:00Z","2020-08-06T06:00:00Z"],"mmsi":["111111111","222222222"],"vessel_name":["A","B"],"longitude":[57.65,57.8],"latitude":[-20.50,-20.3],"is_interpolated":[False,False],"source":["GFW","GFW"],"sampling_interval_minutes":[60.0,60.0]}).to_csv(gfw,index=False)
    prepared=tmp_path/"prepared"; build_blinded_candidates(gfw,prepared/"candidates.csv",prepared/"truth.json")
    env=synthetic_environment(tmp_path/"unused.json"); frame=env.frame.copy(); frame["time_utc"]=pd.date_range("2020-08-05T00:00:00Z",periods=len(frame),freq="1h")
    cache=tmp_path/"environment.json"; cache.write_text(json.dumps({"source":"test","temporal_resolution":"hourly","samples":frame.assign(time_utc=frame["time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")).to_dict(orient="records")}),encoding="utf-8")
    result=search_release_window(slick,cache,prepared/"candidates.csv",tmp_path/"result",ages_hours=(1.5,),current_multipliers=(1.0,),windages=(0.02,),particles=120,ensemble_members=2)
    assert result["status"]=="PASS" and result["answer_key_accessed"] is False
    assert (tmp_path/"result"/"release_time_search.html").exists()
