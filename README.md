# Albion Merchant Toolkit

Az Albion Merchant Toolkit egy magyar nyelvű, közösségi Albion Online
piacelemző és kereskedéstámogató asztali alkalmazás. Segít áttekinteni a
városi árakat, felkutatni a kereskedési lehetőségeket, kiszámolni a crafting
és refining költségeit, valamint nyilvántartani a készletet és a lezárt
kereskedési projekteket.

> A projekt aktív fejlesztés alatt áll.

## Főbb funkciók

- kártyás itemkereső szöveges, kategória-, tier-, enchantment- és
  quality-szűréssel;
- városonkénti piaci árak és az adatok korának megjelenítése;
- több város között kereső Market Scanner;
- crafting receptek, alapanyagok, költségek és rekurzív receptfa;
- refining kalkulátor városi profillal, RRR-rel, focus- és ROI-számítással;
- saját inventory 999 darabos stackkezeléssel;
- külön ár-, crafting- és market-flip kedvencek;
- Order rendszer tervezett, aktív és lezárt állapottal;
- lezáráskor végrehajtott atomi inventory-levonás;
- havi statisztika Order, item és város szerinti bontással;
- bevétel-, költség- és profitgrafikon napi, heti vagy havi idővonallal;
- item- és városonkénti profit-rangsor;
- az Albion Online Data Project adataira épülő helyi piaci adatbázis.

## Telepítés Windowsra

A GitHub Releases oldalról töltsd le az aktuális
`AlbionMerchantToolkit-Setup-<verzió>.exe` fájlt, majd indítsd el a telepítőt.

A telepíthető kiadás tartalmazza:

- a programot;
- a Python futtatókörnyezetet;
- a szükséges Python- és Qt-függőségeket;
- az alap itemkatalógust.

Ezért a használatához **nem kell külön Pythont telepíteni**.

Az alkalmazás jelenleg nincs kereskedelmi kódtanúsítvánnyal aláírva, ezért a
Windows SmartScreen az első indításkor figyelmeztetést jeleníthet meg. Mindig
ennek a repositorynak a Releases oldaláról származó telepítőt használd.

## Helyi adatok

A telepített program nem a védett telepítési könyvtárba ír. A személyes és
folyamatosan változó adatbázisokat itt tárolja:

```text
%LOCALAPPDATA%\AlbionMerchantToolkit\data
```

Itt található többek között:

- `market.db` – helyi piaci árak és történeti adatok;
- `user.db` – inventory, kedvencek, beállítások és Orderek;
- `catalog.db` – a telepítőből első indításkor kimásolt itemkatalógus.

Frissítéskor és újratelepítéskor ezek a felhasználói adatok nem kerülnek
automatikusan törlésre. Ha az új telepítő frissebb beépített katalógust
tartalmaz, a `catalog.db` frissül, miközben a `market.db` és `user.db`
érintetlen marad.

A program korábbi, **Albion Prize Shower** nevű változatáról történő első
indításkor a meglévő `%LOCALAPPDATA%\AlbionPrizeShower\data` tartalmát
automatikusan átmásolja az új adatmappába. Az eredeti könyvtár biztonsági
másolatként megmarad.

## Hibajelentés és naplók

A program forgó naplófájlba rögzíti az indulást, a háttérműveleteket, az
adatfrissítéseket és a hibák részletes nyomát. Telepített verzióban a naplók itt
találhatók:

```text
%LOCALAPPDATA%\AlbionMerchantToolkit\logs
```

Az **Adatközpont → Diagnosztikai ZIP mentése** gomb egy elküldhető csomagot
készít. Ez tartalmazza a naplókat és az alap műszaki környezet adatait, de nem
teszi bele a `catalog.db`, `market.db` vagy `user.db` adatbázisokat. A
**Log mappa megnyitása** gombbal a nyers naplófájlok közvetlenül is elérhetők.

Forráskódból futtatva a naplók a repository `logs` mappájába kerülnek. A
naplófájlok automatikusan rotálódnak, így nem nőnek korlátlanul.

## Indítás forráskódból

Fejlesztéshez Python 3.13 ajánlott.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/pyqt_app.py
```

Vagy használd a gyökérkönyvtárban található `AlbionMerchantToolkit.bat` fájlt.

Az `items.json` és `localization.json` nagy forrásfájlok, ezért nincsenek a
Git repositoryban. A futó program alap itemkatalógusát a verziókezelt
`data/catalog.db` biztosítja.

A két nagy JSON helyi, speciális buildbe az `APS_INCLUDE_SOURCE_JSON=1`
környezeti változóval tehető bele. A normál GitHub-kiadás szándékosan csak a
kész katalógust csomagolja.

## Automatikus tesztek

```powershell
python -m unittest discover -s tests -v
```

A `tests` mappa a számításokat, adatbázis-migrációkat, inventory- és
Order-műveleteket, UI-komponenseket, illetve a főablak felépülését ellenőrzi.
Nem része a végfelhasználói telepítőnek.

## Windows telepítő készítése

A GitHub Actions automatikusan:

1. telepíti a build függőségeit;
2. lefuttatja az automatikus teszteket;
3. PyInstallerrel önálló Windows x64 alkalmazást készít;
4. Inno Setuppal telepítővé csomagolja;
5. feltölti a telepítőt a workflow artifactjai közé.

A `main` ágra történő push minden alkalommal készít egy tesztelhető buildet.
Verziózott kiadáshoz használj `v` előtagú taget:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

Tag esetén a workflow létrehozza – vagy frissíti – a GitHub Release-t, és
hozzácsatolja a telepítőt.

Helyi PyInstaller-build:

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean packaging/AlbionMerchantToolkit.spec
```

## Fejlesztési állapot

Elkészült az Order rendszer, a részletes statisztika és a grafikonos üzleti
áttekintés. Későbbi fejlesztési terv:

- itemkártyák közötti közvetlen kereszt-navigáció az egyes modulok között;
- klánszintű beállítás-, kedvenc- és kereskedésiútvonal-import/export;
- katalógus- és lokalizációfrissítés közvetlenül GitHubról.

A részletes funkcionális és technikai tervek a projekt Markdown
dokumentumaiban találhatók.

## Adatforrás és felelősség

A piaci adatok közösségi adatgyűjtésből származnak, ezért hiányosak vagy
elavultak lehetnek. A program mindenhol jelzi az adatok frissességét, de a
kereskedési döntésekért és az esetleges veszteségekért a felhasználó felel.

Az Albion Online és minden kapcsolódó megjelölés a jogos tulajdonosához
tartozik. Ez egy független közösségi projekt.
