/*
 * Easy Server Cloner — a clone, as it runs.
 *
 * Two servers side by side. The left one is the source; its roles, categories
 * and channels travel across one at a time and land in the target, top down,
 * the way the bot builds them. Click or tap while it runs and the connection
 * "drops"; click again and it resumes from the checkpoint instead of starting
 * over. The server and its channel names are made up.
 */
export default function scene(stage) {
  const { THREE, frame } = stage;
  stage.lights({ ambient: 0.7, key: 2.2, rim: 26 });
  stage.environment(0.8);

  const root = new THREE.Group();
  frame.add(root);
  root.add(stage.halo(stage.accent, 8, 0.22));

  /* ---------------------------------------------------------------- rows */

  // What a server is made of, in the order the bot creates it.
  const ROWS = [
    { kind: 'role', text: 'Admin', color: '#f47b67' },
    { kind: 'role', text: 'Moderator', color: '#5ea1ff' },
    { kind: 'role', text: 'Member', color: '#57d18a' },
    { kind: 'role', text: '@everyone', color: '#9aa3ad' },
    { kind: 'category', text: 'INFORMATION' },
    { kind: 'text', text: 'welcome' },
    { kind: 'text', text: 'rules' },
    { kind: 'text', text: 'announcements' },
    { kind: 'category', text: 'COMMUNITY' },
    { kind: 'text', text: 'general' },
    { kind: 'text', text: 'media' },
    { kind: 'text', text: 'bot-commands' },
    { kind: 'voice', text: 'Lounge' },
    { kind: 'voice', text: 'Gaming' },
  ];
  const WIDTH = 1.62;
  const HEIGHT = 0.165;
  const GAP = 0.2;
  const TOP = ((ROWS.length - 1) * GAP) / 2 - 0.12;

  const paint = (row) => (g, w, h) => {
    g.textBaseline = 'middle';
    if (row.kind === 'role') {
      g.fillStyle = 'rgba(255, 255, 255, 0.07)';
      g.beginPath();
      if (g.roundRect) g.roundRect(0, h * 0.08, w, h * 0.84, h * 0.42);
      else g.rect(0, h * 0.08, w, h * 0.84);
      g.fill();
      g.fillStyle = row.color;
      g.beginPath();
      g.arc(h * 0.55, h / 2, h * 0.22, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = '#f2f5f2';
      g.font = stage.font.body(Math.round(h * 0.5), 600);
      g.fillText(row.text, h * 1.05, h * 0.54);
    } else if (row.kind === 'category') {
      g.fillStyle = 'rgba(242, 245, 242, 0.55)';
      g.font = stage.font.mono(Math.round(h * 0.42), 500);
      g.fillText(`▾ ${row.text}`, h * 0.2, h * 0.56);
    } else {
      g.fillStyle = 'rgba(242, 245, 242, 0.5)';
      g.font = stage.font.body(Math.round(h * 0.56), 600);
      g.fillText(row.kind === 'voice' ? '♪' : '#', h * 0.62, h * 0.54);
      g.fillStyle = 'rgba(242, 245, 242, 0.88)';
      g.font = stage.font.body(Math.round(h * 0.5), 500);
      g.fillText(row.text, h * 1.3, h * 0.54);
    }
  };

  /* ------------------------------------------------------------- servers */

  const server = (x, turn, title) => {
    const group = new THREE.Group();
    group.position.x = x;
    group.rotation.y = turn;
    const glass = new THREE.Mesh(stage.slab(1.95, 3.3, 0.08, 0.16), stage.mat.solid(0x15122b, { roughness: 0.35, metalness: 0.2 }));
    glass.position.z = -0.07;
    group.add(glass, stage.edges(stage.slab(1.95, 3.3, 0.08, 0.16), stage.accentHi, 0.35));
    group.children[1].position.z = -0.07;
    const name = stage.plate(1.6, 0.2, (g, w, h) => {
      g.fillStyle = 'rgba(242, 245, 242, 0.6)';
      g.font = stage.font.mono(Math.round(h * 0.5), 500);
      g.textBaseline = 'middle';
      g.fillText(title, 0, h / 2);
    });
    name.position.set(0, TOP + 0.3, 0.02);
    group.add(name);
    root.add(group);
    return group;
  };
  const source = server(-1.2, 0.38, 'SOURCE · PIXEL GUILD');
  const target = server(1.2, -0.38, 'TARGET · NEW SERVER');

  const rows = ROWS.map((row, index) => {
    const y = TOP - index * GAP;
    const original = stage.plate(WIDTH, HEIGHT, paint(row), { resolution: 300 });
    original.position.set(0, y, 0.03);
    original.renderOrder = 1;
    source.add(original);
    const copy = new THREE.Mesh(original.geometry, original.material.clone());
    copy.position.set(0, y, 0.03);
    copy.material.opacity = 0;
    copy.renderOrder = 1;
    target.add(copy);
    return { original, copy, y, flash: 0 };
  });

  // The one object that is on its way.
  const flyer = new THREE.Mesh(rows[0].original.geometry, rows[0].original.material.clone());
  flyer.visible = false;
  flyer.renderOrder = 2;
  root.add(flyer);
  const trail = stage.halo(stage.accentHi, 1.1, 0.5);
  trail.visible = false;
  root.add(trail);

  let label = '';
  const plate = stage.plate(4.6, 0.36, (g, w, h) => {
    g.fillStyle = '#f2f5f2';
    // The longest line has to fit too: shrink the type until it does.
    let size = Math.round(h * 0.42);
    g.font = stage.font.mono(size);
    while (size > 8 && g.measureText(label).width > w * 0.96) {
      size -= 1;
      g.font = stage.font.mono(size);
    }
    g.textBaseline = 'middle';
    g.textAlign = 'center';
    g.fillText(label, w / 2, h / 2);
  });
  plate.position.set(0, -2.3, 1);
  frame.add(plate);
  const fit = (view) => {
    plate.visible = stage.mode === 'hero' && view.wide;
  };
  stage.onResize(fit);
  fit(stage.view);

  stage.dust({ count: 170, radius: 6.5, color: stage.accentHi, opacity: 0.45 });

  /* ----------------------------------------------------------- behaviour */

  const say = (text) => {
    if (text === label) return;
    label = text;
    plate.userData.redraw();
  };
  const from = new THREE.Vector3();
  const to = new THREE.Vector3();

  let done = 0; // objects that exist in the target
  let flight = 0; // 0..1 of the current object's journey
  let state = 'cloning'; // cloning | dropped | done | clearing
  let resumed = false;
  let rest = 0;

  stage.onClick(() => {
    if (state === 'cloning') {
      state = 'dropped';
    } else if (state === 'dropped') {
      state = 'cloning';
      resumed = true;
    } else if (state === 'done') {
      rest = 99;
    }
  });

  stage.onFrame((time, delta) => {
    root.rotation.y = stage.yaw * 0.5;
    root.rotation.x = 0.08 + stage.pitch;
    root.position.y = 0.2 + Math.sin(time * 0.7) * 0.04;

    if (state === 'cloning') {
      flight += delta / 0.55;
      if (flight >= 1) {
        rows[done].copy.material.opacity = 1;
        rows[done].flash = 1;
        done += 1;
        flight = 0;
        if (done === rows.length) {
          state = 'done';
          rest = 0;
        }
      }
    } else if (state === 'done') {
      rest += delta;
      if (rest > 3) state = 'clearing';
    } else if (state === 'clearing') {
      // A new run starts on an empty target.
      let left = 0;
      for (const row of rows) {
        row.copy.material.opacity = Math.max(0, row.copy.material.opacity - delta * 2.2);
        left += row.copy.material.opacity;
      }
      if (left === 0) {
        done = 0;
        flight = 0;
        resumed = false;
        state = 'cloning';
      }
    }

    // The object in the air travels from its place in the source to its place in the target.
    const flying = state === 'cloning' && done < rows.length;
    flyer.visible = trail.visible = flying;
    if (flying) {
      const row = rows[done];
      if (flyer.material.map !== row.original.material.map) flyer.material.map = row.original.material.map;
      root.updateWorldMatrix(true, true);
      root.worldToLocal(row.original.getWorldPosition(from));
      root.worldToLocal(row.copy.getWorldPosition(to));
      const eased = flight * flight * (3 - 2 * flight);
      flyer.position.lerpVectors(from, to, eased);
      flyer.position.z += Math.sin(eased * Math.PI) * 0.9;
      flyer.rotation.y = THREE.MathUtils.lerp(source.rotation.y, target.rotation.y, eased);
      flyer.material.opacity = 0.95;
      trail.position.copy(flyer.position);
    }

    for (const row of rows) {
      row.flash = Math.max(0, row.flash - delta * 2.5);
      row.copy.scale.setScalar(1 + row.flash * 0.08);
      // While the connection is down the half-built target waits, dimmed.
      if (state === 'dropped' && row.copy.material.opacity > 0) row.copy.material.opacity = 0.45 + Math.sin(time * 4) * 0.12;
      else if (state === 'cloning' && row.copy.material.opacity > 0) row.copy.material.opacity = 1;
    }

    const count = `${done} / ${rows.length} objects`;
    if (state === 'cloning') say(`${resumed ? '/clone resume:True' : '/clone'} · ${count}`);
    else if (state === 'dropped') say(`Interrupted at ${count} — click to resume`);
    else if (state === 'done') say(`Done · ${count}`);
  });
}
