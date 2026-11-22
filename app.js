// Behaviour only (ADR-0002). index.html is complete without this file. Each section has its
// own init: it does everything that can fail first, moves DOM last, and returns true when it
// enhanced the section. A throwing init is logged with its section name and leaves that
// section static; the other sections still upgrade.
(() => {
  "use strict";
  const COPY_LABEL = "Copy";
  const RESET_MS = 1600;
  // Expected outcomes, left to the poster: an autoplay block, a reload, and a clip that failed to
  // load (the browser has already logged that failure itself; S-002 c6).
  const QUIET_PLAY_ERRORS = ["NotAllowedError", "AbortError", "NotSupportedError"];

  // The one shared polite region for Copy results (design 3) sits empty in index.html from load,
  // so a screen reader is watching it before its text changes. This is its only writer.
  const announce = (text) => {
    const status = document.getElementById("status");
    if (!status) throw new Error("the status region #status is missing");
    status.textContent = text;
  };

  // Copy reads the visible <pre> at click time: no command is held here (SR-07). Without a
  // clipboard the line is selected instead.
  const enableCopy = (root) => {
    const buttons = root.querySelectorAll(".code button");
    for (const button of buttons) {
      const pre = button.parentElement.querySelector("pre");
      if (!pre) throw new Error("a Copy button has no command line next to it");
      let timer;
      button.addEventListener("click", async () => {
        let result = "Copied";
        try {
          await navigator.clipboard.writeText(pre.textContent);
        } catch {
          getSelection().selectAllChildren(pre);
          result = "Selected";
        }
        button.textContent = result;
        announce(result);
        clearTimeout(timer);
        timer = setTimeout(() => {
          button.textContent = COPY_LABEL;
          announce("");
        }, RESET_MS);
      });
    }
    for (const button of buttons) button.hidden = false;
    return buttons.length > 0;
  };

  // The clip plays unless the visitor asks for reduced motion. The pause control shows only
  // once the clip really plays, and its name follows the clip's own play and pause events.
  const startClip = async (video) => {
    try {
      await video.play();
      return true;
    } catch (error) {
      if (!QUIET_PLAY_ERRORS.includes(error.name)) console.error("hero clip", error);
      return false;
    }
  };

  const hero = (section) => {
    const video = section.querySelector("video");
    const pause = section.querySelector(".pause");
    const playIcon = pause.querySelector(".i-play");
    const pauseIcon = pause.querySelector(".i-pause");
    const render = () => {
      const name = video.paused ? "Play animation" : "Pause animation";
      pause.setAttribute("aria-label", name);
      pause.title = name;
      playIcon.hidden = !video.paused;
      pauseIcon.hidden = video.paused;
    };
    video.addEventListener("play", render);
    video.addEventListener("pause", render);
    // A decode failure (NotSupportedError) is handled, not ignored: the poster stays and the pause control goes away.
    // Nothing is logged here: a failed load is an expected condition, not a fault of the page.
    video.addEventListener("error", () => { pause.hidden = true; });
    pause.addEventListener("click", () => {
      if (video.paused) startClip(video);
      else video.pause();
    });
    const copying = enableCopy(section);
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
      startClip(video).then((playing) => { pause.hidden = !playing; }); // on failure the poster stays
    }
    return copying;
  };

  const doors = (section) => enableCopy(section);
  // The journey runs on scroll alone (design 3, journey contract): one render per frame reads the
  // scroll position in vh and derives the scene, the settle-in and the agent in the light from it.
  // Geometry is here, in the 1600 x 1200 space of the scene art; every word is in index.html.
  const ART_W = 1600;
  const ART_H = 1200;
  const SETTLE_VH = 33; // scroll a scene takes to settle in
  const ACT_VH = 24; // scroll each agent keeps the light
  const REACHED = 0.6; // share of the viewport where a chapter counts as reached
  const firstAgentVh = (scene) => (scene === 0 ? 20 : 55);
  const RING_RADIUS = 22; // art px, the callout circle drawn in the picture
  const LABEL_OFFSET = 30; // art px from a circle's centre to its label, so the label clears the circle
  const REVEAL_START_VH = 35; // a scene shows its first label and ring this far into the chapter
  const REVEAL_STEP_VH = 3; // each next label and ring follows this much later
  const SPEC_DRAW_VH = 45; // the amber spec line is drawn in full by this point
  const HOLD_ZOOM = 0.035; // slow push-in on a scene that is held whole
  const ZOOM_OUT = 1.6; // the previous scene zooms into its amber object as the next one settles in
  const ZOOM_IN_FROM = 1.25; // the next scene starts this much zoomed and eases to 1
  const CROSSFADE_FROM = 0.35; // settle progress at which the old scene starts to fade out
  const LIGHT_FALLOFF = 1.2; // the work light's radius is the agent box over this, clear to 60% of it
  const DIM_ALPHA = 0.72; // how dark everything outside the work light goes
  const LAST_CHAPTER_TAIL_VH = 47; // the last chapter runs on after its acts, because the picture unpins before it ends
  const CHAPTER_TAIL_VH = 7; // scroll left after the last act of any other chapter
  const revealAt = (k) => REVEAL_START_VH + k * REVEAL_STEP_VH;
  const HIT_PAD = 4; // px a label's hit box extends above and below it (site.css)
  const MIN_APART = 24; // px between two label centres
  const EDGE = 4; // px a label keeps from the frame edge
  const keepInside = (low, high, min, max) => (low < min + EDGE ? min + EDGE - low : high > max - EDGE ? max - EDGE - high : 0);
  const SHORT_VIEWPORT = "(max-height: 600px)";
  const APPROVED = "Approved. Shipped.";
  const SVG_NS = "http://www.w3.org/2000/svg";
  // Tags: x, y is the callout circle centre. side "l" or "t" puts the tag left of or above it;
  // ring false means no circle in the art and x, y is the tag's own corner.
  // Acts: tag is the index of the lit label (-1 for none), box the agent's [x0, y0, x1, y1].
  const SCENES = [
    { tags: [{ t: "You", x: 150, y: 150, ring: false }, { t: "Team Lead", x: 601, y: 125 }, { t: "Architect", x: 1020, y: 180 }, { t: "Security", x: 1310, y: 230 }, { t: "FinOps", x: 1548, y: 296, side: "l" }, { t: "Spec", x: 300, y: 470, spec: true, ring: false }],
      spec: "M 430 428 C 560 300, 650 320, 720 350 S 900 520, 1000 590", focus: [330, 435],
      acts: [{ tag: 0, box: [30, 150, 400, 1000] }, { tag: 1, box: [250, 130, 790, 1000] }, { tag: 2, box: [660, 190, 1050, 1000] }, { tag: 3, box: [1000, 250, 1340, 1000] }, { tag: 4, box: [1320, 290, 1570, 1000] }] },
    { tags: [{ t: "Team Lead", x: 638, y: 114 }, { t: "Architect", x: 980, y: 160 }, { t: "Security", x: 1271, y: 210 }, { t: "FinOps", x: 1464, y: 273, side: "l" }],
      focus: [928, 597],
      acts: [{ tag: 0, box: [390, 130, 700, 1020] }, { tag: 1, box: [680, 190, 970, 1010] }, { tag: 2, box: [950, 210, 1320, 1010] }, { tag: 3, box: [1310, 290, 1560, 1010] }, { tag: -1, box: [10, 270, 410, 640] }] },
    { tags: [{ t: "Team Lead", x: 397, y: 157 }, { t: "Developer", x: 617, y: 247 }, { t: "Developer", x: 1197, y: 270, side: "l" }, { t: "Tester", x: 1410, y: 275 }],
      focus: [940, 640], parts: ["inset(0 50.6% 0 0)", "inset(0 35% 0 49.4%)", "inset(0 0 0 65%)"], // the three machine parts, left to right; act n fits part n - 1
      acts: [{ tag: 0, box: [134, 182, 442, 1018] }, { tag: 1, box: [448, 252, 852, 756] }, { tag: 2, box: [853, 278, 1275, 754] }, { tag: 3, box: [1066, 221, 1526, 1027] }] },
    { tags: [{ t: "Team Lead", x: 150, y: 205, ring: false }, { t: "UX/UI", x: 330, y: 385, ring: false }, { t: "Architect", x: 545, y: 250, ring: false }, { t: "Security", x: 1000, y: 255, ring: false }, { t: "FinOps", x: 1385, y: 315, ring: false }, { t: "Finding", x: 1133, y: 498, side: "t" }],
      focus: [800, 620],
      acts: [{ tag: 2, box: [430, 260, 750, 760] }, { tag: 1, box: [240, 400, 530, 1070] }, { tag: 4, box: [1330, 320, 1570, 970] }, { tag: 3, box: [850, 270, 1310, 720] }, { tag: 5, box: [980, 480, 1430, 1050] }] },
    { tags: [{ t: "Architect", x: 350, y: 150 }, { t: "Developer", x: 617, y: 340 }, { t: "Performance", x: 1200, y: 218 }, { t: "Team Lead", x: 1105, y: 548, ring: false }],
      focus: [620, 540],
      acts: [{ tag: 0, box: [130, 130, 450, 480] }, { tag: 1, box: [230, 320, 1020, 810] }, { tag: 2, box: [1050, 210, 1600, 570] }, { tag: 3, box: [800, 550, 1410, 1080] }] },
    { tags: [{ t: "Architect", x: 568, y: 137, side: "t" }, { t: "Security", x: 795, y: 158, side: "t" }, { t: "Tester", x: 962, y: 177, side: "t" }, { t: "FinOps", x: 1095, y: 217 }, { t: "Team Lead", x: 1262, y: 335, ring: false }, { t: "You", x: 232, y: 318, ring: false }],
      focus: [760, 570],
      acts: [{ tag: 4, box: [930, 240, 1320, 1020] }, { tag: 5, box: [90, 290, 620, 1050] }, { tag: -1, box: [30, 130, 1110, 560] }] },
    { tags: [{ t: "Architect", x: 368, y: 183, side: "t" }, { t: "FinOps", x: 472, y: 218 }, { t: "Security", x: 674, y: 172, side: "t" }, { t: "Tester", x: 884, y: 197 }, { t: "DevOps", x: 568, y: 465, side: "t" }, { t: "Team Lead", x: 1150, y: 440, ring: false }, { t: "You", x: 110, y: 372, ring: false }],
      seal: { t: "Approved", x: 975, y: 505, side: "t", spec: true, ring: false }, // added by Approve
      focus: [975, 575], parts: ["none"], gated: true, // the seal stays grey until you approve
      acts: [{ tag: 6, box: [40, 380, 370, 1140] }, { tag: 5, box: [900, 440, 1380, 1010] }, { tag: 4, box: [370, 430, 700, 960] }] },
  ];

  const make = (tag, className) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  };
  const makeSvg = (tag, attributes) => {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    return node;
  };
  const clamp = (value) => Math.max(0, Math.min(1, value));
  const ease = (x) => x * x * (3 - 2 * x);
  const share = (value, whole) => `${(value / whole) * 100}%`;
  const originOf = ([x, y]) => `${share(x, ART_W)} ${share(y, ART_H)}`;

  // The words of the journey, read from index.html: role texts, and per chapter its name, the
  // "who" line and one line per agent act.
  const readRoles = (template) => {
    const roles = new Map();
    for (const term of template.content.querySelectorAll("dt")) roles.set(term.textContent, term.nextElementSibling.textContent);
    return roles;
  };
  const readScenes = (chapters) => SCENES.map((scene, i) => {
    const lines = [...chapters[i].querySelector("template.acts").content.children];
    if (lines.length !== scene.acts.length) throw new Error(`${chapters[i].id} has ${lines.length} agent lines, expected ${scene.acts.length}`);
    const acts = scene.acts.map((act, k) => ({ ...act, say: lines[k].textContent, name: lines[k].dataset.name }));
    return { ...scene, name: chapters[i].dataset.name, who: chapters[i].dataset.who, acts };
  });

  // A label: a button placed in the art's space. Two labels with one name get an accessible name that
  // tells them apart and still holds the visible text.
  const makeLabel = (tag, roles, onPick, accessibleName) => {
    if (!roles.has(tag.t)) throw new Error(`no role text for the label "${tag.t}"`);
    const button = make("button", `tag${tag.side ? ` ${tag.side}` : ""}${tag.spec ? " spec-tag" : ""}`);
    const offset = tag.ring === false ? 0 : LABEL_OFFSET;
    button.type = "button";
    button.textContent = tag.t;
    if (accessibleName) button.setAttribute("aria-label", accessibleName);
    button.style.left = share(tag.x + (tag.side === "l" ? -offset : tag.side === "t" ? 0 : offset), ART_W);
    button.style.top = share(tag.y + (tag.side === "t" ? -offset : 0), ART_H);
    button.hidden = true;
    button.addEventListener("click", () => onPick(button, tag.t));
    return button;
  };
  const laneNames = (tags) => tags.map((tag) => {
    const same = tags.filter((other) => other.t === tag.t);
    return same.length > 1 ? `${tag.t}, lane ${String.fromCharCode(65 + same.indexOf(tag))}` : null;
  });

  // One layer per scene: the art (picture, grey parts, work light), the rings and spec line, the labels.
  const buildLayer = (scene, slot, chapterId, roles, onPick) => {
    const layer = make("div", "layer");
    const art = make("div", "art");
    const tagsBox = make("div", "tags");
    const dim = make("div", "dim");
    const unfitSource = slot.querySelector("img").dataset.unfit || "";
    if (scene.parts && !unfitSource) throw new Error(`${chapterId} has grey parts but no data-unfit picture`);
    const parts = (scene.parts || []).map((clip) => {
      const part = make("div", "unfit"); // a div, so the layer keeps its one <img>
      part.style.clipPath = clip;
      part.dataset.state = "unfit";
      return part;
    });
    if (scene.gated) parts[0].dataset.sealed = "false";
    const group = makeSvg("g", {});
    const rings = scene.tags.filter((tag) => tag.ring !== false).map((tag) => makeSvg("circle", { cx: tag.x, cy: tag.y, r: RING_RADIUS, class: "ring" }));
    const spec = scene.spec ? makeSvg("path", { d: scene.spec, class: "spec-solid", pathLength: 1 }) : null;
    group.append(...rings, ...(spec ? [spec] : []));
    const overlay = makeSvg("svg", { class: "overlay", viewBox: `0 0 ${ART_W} ${ART_H}`, preserveAspectRatio: "xMidYMid slice", "aria-hidden": "true" });
    overlay.append(group);
    art.append(...parts, dim);
    art.setAttribute("aria-hidden", "true");
    const names = laneNames(scene.tags);
    const labels = scene.tags.map((tag, k) => ({ button: makeLabel(tag, roles, onPick, names[k]) }));
    tagsBox.append(...labels.map((label) => label.button));
    layer.append(art, overlay, tagsBox);
    layer.inert = true;
    return { layer, art, dim, parts, group, rings, spec, labels, tagsBox, unfitSource, unfitLoaded: false };
  };
  // The grey copies load the first time their scene is reached, not with the page.
  const loadUnfit = (layer) => {
    if (layer.unfitLoaded || !layer.parts.length) return;
    layer.unfitLoaded = true;
    for (const part of layer.parts) part.style.backgroundImage = `url("${layer.unfitSource}")`;
  };

  // An ellipse around the agent's box, clear out to 60% of its radius.
  const shineOn = (layer, [x0, y0, x1, y1]) => {
    layer.dim.style.setProperty("--cx", share((x0 + x1) / 2, ART_W));
    layer.dim.style.setProperty("--cy", share((y0 + y1) / 2, ART_H));
    layer.dim.style.setProperty("--rx", share((x1 - x0) / LIGHT_FALLOFF, ART_W));
    layer.dim.style.setProperty("--ry", share((y1 - y0) / LIGHT_FALLOFF, ART_H));
  };

  // Labels keep their place in the art, then are kept inside the frame and pushed apart so that each
  // hit box (the label plus HIT_PAD above and below) is free and two labels are MIN_APART apart (home.md F-3).
  // Offsets use the `translate` property, which composes with the label's own transform.
  const spreadLabels = (layer, frame) => {
    const bounds = frame.getBoundingClientRect();
    const placed = [];
    for (const { button } of layer.labels) {
      button.style.translate = "";
      const r = button.getBoundingClientRect();
      let dx = keepInside(r.left, r.right, bounds.left, bounds.right);
      let dy = keepInside(r.top, r.bottom, bounds.top, bounds.bottom);
      const fits = (centre) => centre - r.height / 2 >= bounds.top + EDGE && centre + r.height / 2 <= bounds.bottom - EDGE;
      for (let pass = 0; pass < 3; pass++) {
        for (const other of placed) {
          const cx = r.left + dx + r.width / 2;
          const cy = r.top + dy + r.height / 2;
          const apart = Math.max(MIN_APART, (r.height + other.h) / 2 + 2 * HIT_PAD);
          if (Math.abs(cx - other.cx) >= (r.width + other.w) / 2 || Math.abs(cy - other.cy) >= apart) continue;
          const up = other.cy - apart;
          const down = other.cy + apart;
          dy += ((cy < other.cy ? fits(up) : !fits(down)) ? up : down) - cy; // the side with room, nearest first
        }
      }
      button.style.translate = `${dx}px ${dy}px`;
      placed.push({ cx: r.left + dx + r.width / 2, cy: r.top + dy + r.height / 2, w: r.width, h: r.height });
    }
  };

  // Scene i settles in (t from 0 to 1) over scene i - 1; p is the progress through the whole chapter.
  const crossfade = (layers, scenes, i, t, p, reduced) => {
    const current = layers[i];
    current.layer.style.zIndex = 2;
    current.art.style.transformOrigin = originOf(scenes[i].focus);
    if (i === 0 || t >= 1) {
      current.layer.style.opacity = 1;
      current.art.style.transform = reduced ? "none" : `scale(${1 + HOLD_ZOOM * p})`;
      return;
    }
    const previous = layers[i - 1];
    const e = ease(t);
    previous.layer.style.zIndex = 1;
    previous.art.style.transformOrigin = originOf(scenes[i - 1].focus);
    if (reduced) {
      previous.layer.style.opacity = 1;
      current.layer.style.opacity = e;
      return;
    }
    const out = ease(clamp((t - CROSSFADE_FROM) / (1 - CROSSFADE_FROM)));
    previous.layer.style.opacity = 1 - out;
    previous.art.style.transform = `scale(${1 + ZOOM_OUT * e})`;
    current.layer.style.opacity = out;
    current.art.style.transform = `scale(${ZOOM_IN_FROM - (ZOOM_IN_FROM - 1) * e})`;
  };

  const journey = (section) => {
    const root = document.documentElement;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)");
    const short = matchMedia(SHORT_VIEWPORT);
    const stage = section.querySelector(".stage");
    const frame = stage.querySelector(".frame");
    const whoCard = stage.querySelector(".who-card");
    const rail = [...stage.querySelectorAll(".rail a")];
    const chapters = [...section.querySelectorAll("ol.chapters > li")];
    const status = section.querySelector("[role=status]");
    const approveRow = section.querySelector(".approve-row");
    const closing = section.querySelector(".closing");
    const roles = readRoles(section.querySelector("#roles"));
    const scenes = readScenes(chapters);
    if (rail.length !== SCENES.length) throw new Error(`${rail.length} rail links, expected ${SCENES.length}`);
    const slots = chapters.map((chapter) => chapter.querySelector(".slot"));
    const homes = slots.map((slot) => ({ parent: slot.parentNode, next: slot.nextSibling }));
    const whoName = make("b", "who-name");
    const whoText = make("span", "who-text");
    let approved = false;
    let picked = null;
    let shown = "";
    let sceneShown = -1;
    let staged = false;

    const showWho = (name, text) => {
      whoName.textContent = name;
      whoText.textContent = text;
    };
    const pick = (button, name) => {
      if (picked) picked.setAttribute("aria-pressed", "false");
      picked = button;
      button.setAttribute("aria-pressed", "true");
      showWho(name, roles.get(name));
      status.textContent = `${name}: ${roles.get(name)}`;
    };
    const layers = scenes.map((scene, i) => buildLayer(scene, slots[i], chapters[i].id, roles, pick));

    // All seven moves or none (Test N3): a throw puts every moved slot back before it is logged.
    const toChapters = () => {
      if (!staged) return;
      slots.forEach((slot, i) => homes[i].parent.insertBefore(slot, homes[i].next));
      staged = false;
    };
    const toStage = () => {
      if (staged) return;
      let moved = 0;
      try {
        for (; moved < slots.length; moved++) layers[moved].art.prepend(slots[moved]);
      } catch (error) {
        for (let i = 0; i < moved; i++) homes[i].parent.insertBefore(slots[i], homes[i].next);
        throw error;
      }
      staged = true;
    };

    const markScene = (i) => {
      stage.dataset.scene = String(i + 1);
      rail.forEach((link, j) => {
        if (j === i) link.setAttribute("aria-current", "step");
        else link.removeAttribute("aria-current");
        link.toggleAttribute("data-passed", j < i);
      });
    };
    const reachedChapter = () => {
      let reached = 0;
      chapters.forEach((chapter, j) => { if (chapter.getBoundingClientRect().top <= innerHeight * REACHED) reached = j; });
      return reached;
    };
    // The agent in the light: which part is fitted, which label is lit, what the card says.
    const showAct = (i, a) => {
      const scene = scenes[i];
      const act = scene.acts[a];
      stage.dataset.phase = a < 0 ? "settle" : a < scene.acts.length ? "act" : "done";
      stage.dataset.lit = act && act.tag >= 0 ? String(act.tag) : "none";
      layers.forEach((layer, j) => {
        if (j === i && act) shineOn(layer, act.box);
        layer.dim.style.setProperty("--a", j === i && act ? DIM_ALPHA : 0);
        layer.parts.forEach((part, m) => {
          part.dataset.state = (scenes[j].gated ? approved : j < i || (j === i && a > m)) ? "fitted" : "unfit";
          if (scenes[j].gated) part.dataset.sealed = String(approved);
        });
        layer.labels.forEach((label, m) => label.button.classList.toggle("act", j === i && !!act && act.tag === m));
      });
      if (picked) picked.setAttribute("aria-pressed", "false");
      picked = null;
      showWho(act ? act.name || scene.tags[act.tag].t : `Step ${i + 1} · ${scene.name}`, act ? act.say : scene.who);
    };

    const draw = () => {
      const i = reachedChapter();
      markScene(i);
      if (short.matches) return; // the static layout: chapters only, no layers, acts or labels
      const box = chapters[i].getBoundingClientRect();
      const into = innerHeight * REACHED - box.top;
      const v = Math.max(0, (into / innerHeight) * 100); // how far into this chapter, in vh
      const t = i === 0 ? 1 : clamp(v / SETTLE_VH);
      const a = Math.min(scenes[i].acts.length, Math.max(-1, Math.floor((v - firstAgentVh(i)) / ACT_VH)));
      layers.forEach((layer) => {
        layer.layer.style.opacity = 0;
        layer.art.style.transform = "none";
        layer.layer.style.zIndex = 0;
      });
      crossfade(layers, scenes, i, t, clamp(into / box.height), reduce.matches);
      const changedScene = sceneShown !== -1 && i !== sceneShown; // the first draw is not a chapter change
      if (i !== sceneShown) {
        sceneShown = i;
        layers.forEach((layer, j) => {
          layer.layer.inert = j !== i;
          layer.labels.forEach((label) => { label.button.hidden = j !== i; });
        });
        loadUnfit(layers[i]);
        spreadLabels(layers[i], frame);
      }
      layers.forEach((layer, j) => {
        layer.group.classList.toggle("on", j === i && t >= 1);
        if (j !== i) return;
        layer.labels.forEach((label, k) => label.button.classList.toggle("on", reduce.matches || i === 0 ? t >= 1 : v > revealAt(k)));
        layer.rings.forEach((ring, k) => ring.classList.toggle("on", i === 0 ? true : v > revealAt(k)));
        if (layer.spec) layer.spec.style.strokeDashoffset = 1 - (reduce.matches ? 1 : clamp(v / SPEC_DRAW_VH));
      });
      const key = `${i}:${a}:${approved}`;
      if (key === shown) return;
      shown = key;
      showAct(i, a);
      if (changedScene && !approved) status.textContent = `${whoName.textContent}: ${whoText.textContent}`; // on a chapter change only
    };

    // Where the rail lands: the whole scene, just before its first agent steps up. In the static layout, the chapter itself.
    const goTo = (i, behavior) => {
      if (short.matches) {
        chapters[i].scrollIntoView({ block: "start", behavior });
        return;
      }
      const top = chapters[i].getBoundingClientRect().top + scrollY - innerHeight * REACHED + (innerHeight * (firstAgentVh(i) - 8)) / 100;
      scrollTo({ top, behavior });
    };

    // A layout change (resize, or the 600 px height break) moves the chapters, so the page puts the reader
    // back in the scene they were in; scroll anchoring is not relied on.
    const applyLayout = () => {
      const stacked = short.matches;
      if (stacked) toChapters();
      else toStage();
      chapters.forEach((chapter, i) => {
        chapter.style.minHeight = stacked ? "" : `${firstAgentVh(i) + ACT_VH * scenes[i].acts.length + (i === chapters.length - 1 ? LAST_CHAPTER_TAIL_VH : CHAPTER_TAIL_VH)}vh`;
      });
      frame.hidden = stacked;
      whoCard.hidden = stacked;
      approveRow.hidden = stacked;
      closing.hidden = !stacked && !approved;
      if (stacked) root.style.removeProperty("--stage-h");
      else root.style.setProperty("--stage-h", `${stage.offsetHeight}px`); // on a phone the chapter text sits under the pinned picture
    };
    const relayout = () => {
      const before = Number(stage.dataset.scene) - 1;
      applyLayout();
      if (!short.matches && sceneShown !== -1) spreadLabels(layers[sceneShown], frame); // the frame has a new size
      draw();
      if (Number(stage.dataset.scene) - 1 === before) return;
      goTo(before, "instant");
      draw();
    };

    const approve = make("button", "btn amber-btn approve");
    approve.type = "button";
    approve.textContent = "Approve the release";
    approve.addEventListener("click", () => {
      if (approved) return;
      approved = true;
      const last = layers.length - 1;
      const seal = { button: makeLabel(scenes[last].seal, roles, pick) };
      seal.button.classList.add("stamped");
      seal.button.hidden = sceneShown !== last;
      layers[last].labels.push(seal);
      layers[last].tagsBox.append(seal.button);
      if (sceneShown === last) spreadLabels(layers[last], frame);
      approve.setAttribute("aria-disabled", "true");
      approve.textContent = APPROVED;
      status.textContent = APPROVED;
      closing.hidden = false;
      closing.querySelector("a").focus({ preventScroll: true });
      if (Number(stage.dataset.scene) !== SCENES.length) goTo(last, reduce.matches ? "auto" : "smooth");
      draw();
    });
    rail.forEach((link, i) => link.addEventListener("click", (event) => {
      if (short.matches) return; // the plain #step-N jump
      event.preventDefault();
      goTo(i, reduce.matches ? "auto" : "smooth");
    }));

    // Everything that can fail is done; now the DOM moves.
    if (!short.matches) toStage();
    frame.append(...layers.map((layer) => layer.layer));
    whoCard.append(whoName, whoText);
    approveRow.append(approve);
    applyLayout();
    draw();

    let frameId = 0;
    let layoutChanged = false;
    const queue = () => {
      if (frameId) return;
      frameId = requestAnimationFrame(() => {
        frameId = 0;
        if (layoutChanged) {
          layoutChanged = false;
          relayout();
        } else draw();
      });
    };
    const queueLayout = () => {
      layoutChanged = true;
      queue();
    };
    addEventListener("scroll", queue, { passive: true });
    addEventListener("resize", queueLayout);
    short.addEventListener("change", queueLayout);
    reduce.addEventListener("change", queue);
    return true;
  };
  // The five request texts become buttons. A pick swaps the answer panel for a clone of its
  // <template data-answer="N"> (N is the 1-based position of the request); the request with no template
  // is the default, whose answer already sits in the panel and is kept to come back to.
  const triage = (section) => {
    const picks = [...section.querySelectorAll("ul.picks > li")];
    const answer = section.querySelector("#triage-answer");
    if (!answer) throw new Error("the triage answer panel #triage-answer is missing");
    const answers = new Map([...section.querySelectorAll("template[data-answer]")].map((template) => [Number(template.dataset.answer), [...template.content.children]]));
    const home = picks.findIndex((_, i) => !answers.has(i + 1));
    if (home === -1 || answers.size !== picks.length - 1) throw new Error(`${picks.length} requests need ${picks.length - 1} answer templates, found ${answers.size}`);
    answers.set(home + 1, [...answer.children].map((node) => node.cloneNode(true)));
    const buttons = picks.map((item, i) => {
      const button = make("button", "pick");
      button.type = "button";
      button.textContent = item.textContent.trim();
      button.setAttribute("aria-pressed", String(i === home));
      button.addEventListener("click", () => {
        if (button.getAttribute("aria-pressed") === "true") return;
        buttons.forEach((other) => other.setAttribute("aria-pressed", String(other === button)));
        answer.replaceChildren(...answers.get(i + 1).map((node) => node.cloneNode(true)));
      });
      return button;
    });
    picks.forEach((item, i) => item.replaceChildren(buttons[i]));
    return true;
  };
  // The generated list stays the one source: each <li> gets a button, and only the pressed agent's <li> shows.
  // Nothing is copied; the buttons are built from the headings and model lines already in the list.
  const MODEL_MARKS = { haiku: "h", sonnet: "s", opus: "o" };
  const roster = (section) => {
    const group = section.querySelector(".names");
    const items = [...section.querySelectorAll("ul.roster > li")];
    if (!group || !items.length) throw new Error("the roster list or its button group is missing");
    const select = (chosen) => items.forEach((item, i) => {
      item.hidden = i !== chosen;
      buttons[i].setAttribute("aria-pressed", String(i === chosen));
    });
    const buttons = items.map((item, i) => {
      const yours = item.querySelector(".excerpt") !== null; // only the "your agent" entry holds a file excerpt
      const button = make("button", yours ? "name plus" : "name");
      button.type = "button";
      button.textContent = `${yours ? "+ " : ""}${item.querySelector("h3").textContent}`;
      // the label may be a phrase ("Your session's model (Opus by default)"): the first model word decides the mark
      const model = item.querySelector("dd").textContent.toLowerCase().match(/haiku|sonnet|opus/);
      button.dataset.m = model ? MODEL_MARKS[model[0]] : "";
      button.addEventListener("click", () => {
        select(i);
        // on a phone the panel sits below the whole button column: bring it into view (UX F-4)
        if (matchMedia("(max-width: 520px)").matches) {
          item.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
        }
      });
      return button;
    });
    group.append(...buttons);
    group.hidden = false;
    select(0);
    return true;
  };

  const sections = [
    ["hero", "#hero", hero],
    ["doors", ".doors", doors],
    ["journey", "#how", journey],
    ["triage", "#triage", triage],
    ["roster", "#team", roster],
  ];

  for (const [name, selector, init] of sections) {
    const section = document.querySelector(selector);
    if (!section) {
      console.error(`section "${name}" not found (${selector})`);
      continue;
    }
    try {
      if (init(section)) section.dataset.enhanced = "";
    } catch (error) {
      console.error(`init failed for section "${name}"`, error);
    }
  }
})();
