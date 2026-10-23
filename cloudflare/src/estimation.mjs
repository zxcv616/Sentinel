// Same windowed horizontal Kalman model as sentinel.estimation.
export const estimationDefaults={position_sigma_m:100,velocity_sigma_mps:50,acceleration_sigma_mps2:2,prediction_seconds:60,filter_reset_seconds:120};
const R=6371008.8,rad=x=>x*Math.PI/180,deg=x=>x*180/Math.PI,wrap=x=>((x+180)%360+360)%360-180;
function predict([x,v,p,c,w],t,a){const q=a*a;return [x+t*v,v,p+2*t*c+t*t*w+q*t**4/4,c+t*w+q*t**3/2,w+q*t*t];}
function update([x,v,p,c,w],z,r){const d=p+r;return [x+p/d*(z-x),v+c/d*(z-x),p*r/d,c*r/d,Math.max(0,w-c*c/d)];}
export function estimate(history,timestamp,settings={}){
  const config={...estimationDefaults,...settings};
  if(!history.length)return null;
  let east,north,previous,lat0,lon0,scale;
  for(const o of history){
    if(previous&&o.timestamp<=previous.timestamp)continue;
    if(!previous||o.timestamp-previous.timestamp>config.filter_reset_seconds||o.status!==previous.status){
      lat0=o.lat;lon0=o.lon;scale=Math.cos(rad(lat0));if(Math.abs(scale)<.01)return null;
      const speed=o.speed!==null&&o.heading!==null&&o.status!=='ground'?o.speed:0,heading=rad(o.heading||0);
      east=[0,speed*Math.sin(heading),config.position_sigma_m**2,0,config.velocity_sigma_mps**2];
      north=[0,speed*Math.cos(heading),config.position_sigma_m**2,0,config.velocity_sigma_mps**2];
    }else{
      const dt=o.timestamp-previous.timestamp;
      east=update(predict(east,dt,config.acceleration_sigma_mps2),R*scale*rad(wrap(o.lon-lon0)),config.position_sigma_m**2);
      north=update(predict(north,dt,config.acceleration_sigma_mps2),R*rad(o.lat-lat0),config.position_sigma_m**2);
    }
    previous=o;
  }
  const position=(e,n)=>({lat:lat0+deg(n[0]/R),lon:wrap(lon0+deg(e[0]/(R*scale))),uncertainty_radius_m:Math.sqrt(5.991*Math.max(0,e[2],n[2]))});
  const age=timestamp-previous.timestamp;
  const prediction=previous.status!=='ground'&&age>0&&age<=config.prediction_seconds?{...position(predict(east,age,config.acceleration_sigma_mps2),predict(north,age,config.acceleration_sigma_mps2)),timestamp,horizon_seconds:age}:null;
  return {method:'horizontal_cv_kalman',filtered:{...position(east,north),timestamp:previous.timestamp},prediction,max_horizon_seconds:config.prediction_seconds};
}
