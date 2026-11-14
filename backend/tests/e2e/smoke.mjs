// Duman testi: React arayüzünü (Vite :5173 -> Python API :8000) gerçek tarayıcıda gezer,
// konsol hatalarını toplar ve her sekmenin ekran görüntüsünü out/ klasörüne yazar.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const BASE = process.env.AETHER_UI ?? "http://127.0.0.1:5173/aether";
const EXE = process.env.CHROMIUM ?? "/usr/sbin/chromium-browser";
const OUT = fileURLToPath(new URL("./out/", import.meta.url));
mkdirSync(OUT, { recursive: true });
const out = (n) => `${OUT}${n}.png`;

const browser = await chromium.launch({ executablePath: EXE, args: ["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"] });
const page = await browser.newPage({ viewport: { width: 1680, height: 1100 } });
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(`[console] ${m.text()}`); });
page.on("pageerror", (e) => errors.push(`[pageerror] ${e.message}`));
page.on("requestfailed", (r) => { if (!/cartocdn|events\.mapbox/.test(r.url())) errors.push(`[reqfail] ${r.url()}`); });

await page.goto(BASE, { waitUntil: "load" });
await page.waitForSelector("video", { timeout: 30000 });   // açılış videosu: Esc ile geç, teste engel olmasın
await page.keyboard.press("Escape");
await page.waitForSelector("video", { state: "detached", timeout: 10000 });
await page.waitForSelector("text=ham tespit", { timeout: 120000 });
await page.waitForTimeout(2500);
await page.screenshot({ path: out("0-default") });
await page.click("text=Canlı NASA FIRMS");
await page.waitForSelector("text=nokta (son 24 saat)", { timeout: 120000 });
await page.waitForSelector('[role="status"]:has-text("nokta gösteriliyor")', { timeout: 120000 });
await page.waitForTimeout(4000);
const stat = async () => (await page.locator("[class*=mapStats]").first().innerText()).replace(/\s+/g, " ");
const status = async () => (await page.locator('[role="status"]').first().innerText()).replace(/\s+/g, " ");
console.log("live stats:", await stat());
console.log("live status:", await status());
await page.screenshot({ path: out("1-live") });

// süzgeçler: düşük güveni kapat, sensör değiştir, sıcaklık alt sınırını yükselt
const before = await stat();
await page.getByLabel(/Düşük \(Low\)/).uncheck();
await page.waitForTimeout(1500);
console.log("after uncheck LOW:", before, "->", await stat());
await page.getByRole("button", { name: "VIIRS", exact: true }).click();
await page.waitForTimeout(1500);
console.log("VIIRS only:", await stat(), "|", await status());
await page.screenshot({ path: out("1a-live-viirs") });
await page.getByRole("button", { name: "MODIS + VIIRS", exact: true }).click();
await page.getByRole("button", { name: "Süzgeçleri sıfırla" }).click();
await page.waitForTimeout(1500);
console.log("after reset:", await stat());

// yakınlaş + noktaya tıkla (kümeye tıklayınca açılır; imleç 'pointer' olan yerleri tara)
let popup = false;
for (let round = 0; round < 14 && !popup; round++) {
  const bx = await page.locator(".mapboxgl-map").first().boundingBox();
  let found = null;
  scanLive: for (let y = bx.y + 30; y < bx.y + bx.height - 30; y += 14) {
    for (let x = bx.x + 30; x < bx.x + bx.width - 70; x += 14) {
      await page.mouse.move(x, y);
      const cur = await page.locator(".mapboxgl-canvas").first().evaluate((e) => e.style.cursor);
      if (cur === "pointer") { found = [x, y]; break scanLive; }
    }
  }
  if (!found) { await page.mouse.move(bx.x + bx.width / 2, bx.y + bx.height / 2); await page.mouse.wheel(0, -400); await page.waitForTimeout(1200); continue; }
  await page.mouse.click(found[0], found[1]);
  await page.waitForTimeout(1500);
  popup = await page.locator(".firePop").first().isVisible().catch(() => false);
}
console.log("point popup opened:", popup);
await page.screenshot({ path: out("1b-live-popup") });
await page.getByRole("button", { name: /Tema/ }).click();
await page.waitForTimeout(3500);
await page.screenshot({ path: out("1c-live-dark") });
await page.getByRole("button", { name: /Tema/ }).click();
await page.waitForTimeout(2500);

// benchmark moduna geç
await page.click("text=Benchmark 2001-2026");
await page.waitForSelector("text=ham tespit", { timeout: 90000 });
await page.waitForTimeout(3000);
await page.screenshot({ path: out("1-map") });
await page.getByRole("button", { name: /Tema/ }).click();
await page.waitForTimeout(3500);
await page.screenshot({ path: out("1b-dark") });
await page.getByRole("button", { name: "Ayarlar" }).click();
await page.waitForTimeout(400);
await page.screenshot({ path: out("1c-settings") });
await page.keyboard.press("Escape");
for (const tab of ["Yanma takvimi", "Model ve doğrulama"]) {
  await page.getByRole("button", { name: tab }).click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: out("1d-dark-" + tab.split(" ")[0]) });
}
await page.getByRole("button", { name: "Görev haritası" }).click();
await page.getByRole("button", { name: /Tema/ }).click();
await page.waitForTimeout(3000);

// altıgene tıkla (haritanın ortasında veri olmayabilir; tüm canvas üzerinde tara)
const box = await page.locator(".mapboxgl-map").first().boundingBox();
let hit = false;
scan: for (let y = box.y + 20; y < box.y + box.height - 20; y += 12) {
  for (let x = box.x + 20; x < box.x + box.width - 60; x += 12) {
    await page.mouse.move(x, y);
    if (await page.locator(".hexTip").first().isVisible().catch(() => false)) {
      await page.mouse.click(x, y);
      await page.waitForTimeout(800);
      hit = await page.locator("h3:has-text(\"Altıgen\")").first().isVisible().catch(() => false);
      break scan;
    }
  }
}
console.log("hex inspector opened:", hit);
await page.screenshot({ path: out("2-hex") });
await page.getByRole("button", { name: "VIIRS", exact: true }).click();
await page.waitForTimeout(600);
await page.screenshot({ path: out("2b-viirs") });
await page.getByRole("button", { name: "MODIS + VIIRS", exact: true }).click();

// ROI çiz: poligon (haritanın sol panelinde 4 köşe) + çift tıkla
await page.click('button:has-text("Poligon çiz")');
if (await page.getByRole("button", { name: "Kapat" }).isVisible().catch(() => false)) await page.getByRole("button", { name: "Kapat" }).click();
await page.waitForTimeout(400);
const b = await page.locator(".mapboxgl-map").first().boundingBox();
const pts = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]];
for (const [fx, fy] of pts) { await page.mouse.click(b.x + b.width * fx, b.y + b.height * fy); await page.waitForTimeout(200); }
await page.mouse.dblclick(b.x + b.width * 0.3, b.y + b.height * 0.7);
await page.waitForTimeout(3000);
await page.screenshot({ path: out("3-roi") });

for (const [label, name, wait] of [
  ["Yapısal kırılma", "4-break", 3000], ["Yanma takvimi", "5-calendar", 3500],
  ["Sıcak nokta açıklayıcı", "6-explain", 3500], ["Model ve doğrulama", "8-model", 3500],
]) {
  await page.click(`nav >> text=${label}`);
  await page.waitForTimeout(wait);
  await page.screenshot({ path: out(name), fullPage: false });
}
await page.click("nav >> text=Sıcak nokta açıklayıcı");
await page.click('button:has-text("Harmonize et")');
await page.waitForTimeout(2500);
await page.screenshot({ path: out("6b-harmonize") });

await page.click("text=Temizle");
await page.click("nav >> text=Brifing");
await page.click('button:has-text("Mevcut kapsam için üret")');
await page.waitForTimeout(3500);
await page.screenshot({ path: out("7-briefing") });

console.log("ERRORS:\n" + (errors.join("\n") || "none"));
await browser.close();
