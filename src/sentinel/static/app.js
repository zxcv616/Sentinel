'use strict';
const el = id => document.getElementById(id);
const colors = {active: '#bdd6c8', gap: '#dfb06c', lost: '#7c8d96'};
let snapshot = null, selected = null, map = null, bounds = null, first = true;
let lastMessage = 0, socket = null, retry = 1000;
const layers = new Map();
const alertLayers = new Map();
const keyOf = track => `${track.source}:${track.object_id}`;
function fit() { if (map && bounds) map.fitBounds(bounds, {padding: [30, 30]}); }
function icon(track) {
  const heading = track.position.heading;
  const shape = heading === null ? '<circle cx="14" cy="14" r="6"/>' : '<path d="M14 4 L20 23 L14 18 L8 23 Z"/>';
  return L.divIcon({className: 'aircraft-icon', iconSize: [23,23], iconAnchor: [11.5,11.5],
    html: `<svg width="23" height="23" viewBox="0 0 28 28" style="transform:rotate(${heading ?? 0}deg)" fill="${colors[track.state]}" stroke="#121c21" stroke-width="1">${shape}</svg>`});
}
function select(track) {
  selected = keyOf(track);
  if (map) map.panTo([track.position.lat, track.position.lon]);
  render();
}
function details(track) {
  el('detail').hidden = !track;
  if (!track) return;
  el('detail-title').textContent = track.object_id.toUpperCase();
  const p = track.position, fmt = (v, unit) => v === null ? 'Unknown' : `${Math.round(v).toLocaleString()} ${unit}`;
  const fields = {State: track.state, Altitude: fmt(p.altitude, 'm'), Speed: fmt(p.speed, 'm/s'), Heading: fmt(p.heading, '°'),
    Squawk: p.squawk ?? 'Unknown', Status: p.status ?? 'Unknown', Position: `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`,
    'Last position': new Date(track.last_seen * 1000).toLocaleTimeString(), 'Trail points': track.history.length};
  el('detail-fields').replaceChildren();
  for (const [label,value] of Object.entries(fields)) {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = label; dd.textContent = value; el('detail-fields').append(dt,dd);
  }
}
function renderAlerts(query) {
  const alerts = (snapshot.alerts || []).filter(a => a.object_id.includes(query));
  el('alert-count').textContent = (snapshot.alerts || []).length;
  el('alerts-empty').hidden = alerts.length > 0;
  el('alerts-empty').textContent = query ? 'No matching alert events.' : 'No alerts in this session.';
  el('alerts').replaceChildren();
  const ids = new Set(alerts.map(a => a.id));
  for (const [id, layer] of alertLayers) {
    if (!ids.has(id)) { map.removeLayer(layer); alertLayers.delete(id); }
  }
  for (const alert of alerts) {
    const title = alert.type === 'emergency_squawk' ? `Squawk ${alert.details.squawk}` : 'Signal gap';
    const stamp = new Date(alert.timestamp * 1000).toLocaleTimeString();
    const label = `${title} · ${alert.object_id.toUpperCase()}`;
    let layer = alertLayers.get(alert.id);
    if (map && alert.lat !== null && alert.lon !== null && !layer) {
      const popup = document.createElement('div');
      popup.textContent = `${label} — ${stamp}. ${alert.details.message}`;
      layer = L.circleMarker([alert.lat, alert.lon], {radius: 14, weight: 2, fillOpacity: 0.12,
        color: alert.type === 'emergency_squawk' ? '#ed526b' : '#da8615'}).addTo(map).bindPopup(popup);
      alertLayers.set(alert.id,layer);
    }
    const button = document.createElement('button');
    button.className = `alert-row ${alert.type}`;
    const heading = document.createElement('strong'), detail = document.createElement('span');
    heading.textContent = label;
    detail.textContent = `${stamp} · ${alert.details.message}${alert.lat === null ? ' · Position unavailable' : ''}`;
    button.append(heading,detail);
    button.onclick = () => {
      if (map && alert.lat !== null && alert.lon !== null) { map.setView([alert.lat,alert.lon],Math.max(map.getZoom(),10)); layer.openPopup(); }
    };
    el('alerts').append(button);
  }
}
function render() {
  if (!snapshot) return;
  for (const state of ['active','gap','lost']) el(state).textContent = snapshot.counts[state];
  el('source').textContent = snapshot.source.message;
  el('updated').textContent = snapshot.source.last_success ? `Last poll ${new Date(snapshot.source.last_success * 1000).toLocaleTimeString()}` : 'No successful poll yet';
  const query = el('search').value.trim().toLowerCase();
  renderAlerts(query);
  const tracks = snapshot.tracks.filter(t => t.object_id.includes(query));
  const visible = new Set(tracks.map(keyOf));
  for (const [key,layer] of layers) {
    if (!visible.has(key)) { map.removeLayer(layer.marker); map.removeLayer(layer.trail); layers.delete(key); }
  }
  el('aircraft').replaceChildren();
  for (const track of tracks) {
    const key = keyOf(track), pos = [track.position.lat, track.position.lon];
    if (map) {
      let layer = layers.get(key);
      if (!layer) {
        const label = document.createElement('span'); label.textContent = track.object_id.toUpperCase();
        layer = {marker: L.marker(pos).addTo(map).bindTooltip(label), trail: L.polyline([]).addTo(map)};
        layer.marker.on('click', () => { const current = snapshot.tracks.find(t => keyOf(t) === key); if (current) select(current); });
        layers.set(key, layer);
      }
      layer.marker.setLatLng(pos).setIcon(icon(track)).setOpacity(track.state === 'lost' ? 0.55 : 1);
      layer.trail.setLatLngs(el('trails').checked ? track.history.map(p => [p.lat,p.lon]) : []);
      layer.trail.setStyle({color: colors[track.state], weight: selected === key ? 3 : 1, opacity: selected === key ? 0.9 : 0.35, dashArray: track.state === 'active' ? null : '5 6'});
    }
    const button = document.createElement('button'); button.className = `aircraft-row${selected === key ? ' selected' : ''}`;
    const id = document.createElement('strong'), state = document.createElement('span');
    id.textContent = track.object_id.toUpperCase(); state.textContent = track.state;
    button.append(id,state); button.onclick = () => select(track); el('aircraft').append(button);
  }
  el('empty').hidden = tracks.length > 0;
  el('empty').textContent = query ? 'No matching aircraft.' : 'No aircraft positions available yet.';
  const chosen = snapshot.tracks.find(t => keyOf(t) === selected);
  if (!chosen) selected = null;
  details(chosen);
}
function connection(text, connected=false) { el('connection').textContent = text; el('connection').classList.toggle('connected',connected); }
function connect() {
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/tracks`);
  socket.onmessage = event => {
    snapshot = JSON.parse(event.data); lastMessage = Date.now(); retry = 1000;
    connection('Map connected',true);
    const b = snapshot.bounds; bounds = [[b.lamin,b.lomin],[b.lamax,b.lomax]];
    el('coverage-label').textContent = `${b.lamin.toFixed(1)}° to ${b.lamax.toFixed(1)}° / ${b.lomin.toFixed(1)}° to ${b.lomax.toFixed(1)}°`;
    if (first) { fit(); first = false; }
    render();
  };
  socket.onclose = () => { connection('Disconnected · reconnecting'); setTimeout(connect,retry); retry = Math.min(retry * 2,30000); };
  socket.onerror = () => socket.close();
}
if (typeof L !== 'undefined') {
  map = L.map('map').setView([38,-122],8);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).on('tileerror', () => {
      el('map-error').hidden = false; el('map-error').textContent = 'Some map tiles could not load. Aircraft data remains available.';
    }).addTo(map);
} else {
  el('map-error').hidden = false; el('map-error').textContent = 'Map library could not load. Check your connection; aircraft data is available in the list.';
}
el('fit').onclick = fit;
el('search').oninput = render;
el('trails').onchange = render;
setInterval(() => {
  if (lastMessage && Date.now() - lastMessage > 5000) {
    connection('Map updates interrupted · positions may be stale');
    if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  }
},1000);
connect();
