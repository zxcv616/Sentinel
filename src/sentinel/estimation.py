"""Windowed horizontal constant-velocity Kalman estimate; never alters observations.

Independent east/north position-velocity filters, local tangent approximation.
Process noise is white acceleration (m/s²); covariance describes model uncertainty,
not a calibrated confidence guarantee. Rebuilt from bounded history for replay parity.
"""
import math

RADIUS = 6371008.8


def axis_predict(s, dt, acceleration):
    x, v, p, c, w = s
    q = acceleration ** 2
    return [x + dt*v, v, p+2*dt*c+dt*dt*w+q*dt**4/4,
            c+dt*w+q*dt**3/2, w+q*dt*dt]


def axis_update(s, measurement, variance):
    x, v, p, c, w = s
    innovation = measurement-x
    denominator = p+variance
    return [x+p/denominator*innovation, v+c/denominator*innovation,
            p*variance/denominator, c*variance/denominator,
            max(0, w-c*c/denominator)]


def estimate(history, timestamp, config):
    """Return filtered position and optional prediction at the supplied event time."""
    observations = list(history)
    if not observations:
        return None
    east = north = None
    previous = None
    for o in observations:
        if previous is not None and o.timestamp <= previous.timestamp:
            continue
        if previous is None or o.timestamp-previous.timestamp > config.filter_reset_seconds or o.status != previous.status:
            lat0, lon0 = o.lat, o.lon
            scale = math.cos(math.radians(lat0))
            if abs(scale) < .01:
                return None  # Local projection is unsuitable close to the poles.
            speed = o.speed if o.speed is not None and o.heading is not None and o.status != 'ground' else 0
            heading = math.radians(o.heading or 0)
            east = [0, speed*math.sin(heading), config.position_sigma_m**2, 0, config.velocity_sigma_mps**2]
            north = [0, speed*math.cos(heading), config.position_sigma_m**2, 0, config.velocity_sigma_mps**2]
        else:
            dt = o.timestamp-previous.timestamp
            dx = RADIUS*scale*math.radians((o.lon-lon0+180)%360-180)
            dy = RADIUS*math.radians(o.lat-lat0)
            east = axis_update(axis_predict(east, dt, config.acceleration_sigma_mps2), dx, config.position_sigma_m**2)
            north = axis_update(axis_predict(north, dt, config.acceleration_sigma_mps2), dy, config.position_sigma_m**2)
        previous = o

    def position(e, n):
        return {'lat': lat0+math.degrees(n[0]/RADIUS),
                'lon': (lon0+math.degrees(e[0]/(RADIUS*scale))+180)%360-180,
                # 95% chi-square radius using the larger horizontal eigenvalue.
                'uncertainty_radius_m': math.sqrt(5.991*max(0, e[2], n[2]))}

    age = timestamp-previous.timestamp
    prediction = None
    if previous.status != 'ground' and 0 < age <= config.prediction_seconds:
        prediction = {**position(axis_predict(east, age, config.acceleration_sigma_mps2),
                                  axis_predict(north, age, config.acceleration_sigma_mps2)),
                      'timestamp': timestamp, 'horizon_seconds': age}
    return {'method': 'horizontal_cv_kalman', 'filtered': {**position(east, north), 'timestamp': previous.timestamp},
            'prediction': prediction, 'max_horizon_seconds': config.prediction_seconds}
