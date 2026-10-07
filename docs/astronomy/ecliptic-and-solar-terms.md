# Ecliptic frames, tropical zodiac, and solar terms

Part of the [Astronomy documentation series](README.md). For engine interfaces, runtime contracts,
and numerical tolerances, see [Astronomy calculations](calculations.md). For the implemented dial,
see [Orloj dial foundation](../orloj.md).

This reference compares tropical sign longitudes with the 24 Solar Terms (二十四節氣) under
Dingqi (定氣法). It also presents a named tropical correspondence using 十二次 and an attributed
Da Liu Ren monthly-general convention. These cultural correspondences do not equate historical
stellar sectors with tropical signs or establish astrological efficacy. This is documentation for
[#65](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/65), supporting
[#41](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/41) and
[#43](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/43); it adds no runtime calculations.

## 1. Astronomical foundation and longitude convention

For this comparison, $\lambda_\odot \in [0^\circ, 360^\circ)$ denotes the Sun's **geocentric
apparent ecliptic longitude of date**, measured eastward from the equinox of date. Tropical signs
are twelve successive $30^\circ$ intervals starting with Aries at $0^\circ$.

The [Hong Kong Observatory's solar-term reference][hko] gives all 24 names and longitudes and
explains the division of the ecliptic into $15^\circ$ intervals. In Dingqi, term instants occur at
$\lambda_\odot = 15^\circ n$, for $n = 0,\ldots,23$. Equal angular intervals need not take equal
time. The table uses the apparent convention for both signs and terms; exact ingress/term
correspondence requires **the same longitude convention**.

### Shared equinox anchor

In this shared convention, $0^\circ$ is Aries ingress and Chunfen (春分). The sign index is
$\lfloor\lambda_\odot/30^\circ\rfloor$; the interval beginning at the preceding solar term has
index $\lfloor\lambda_\odot/15^\circ\rfloor$. These are longitude partitions, not IAU constellation
boundaries.

### Coordinate terminology and the dial contract

The [Orloj contract](../orloj.md#astronomical-frame) and
[engine calculation specification](calculations.md#coordinate-frames) use **geometric** ecliptic
longitude in the true ecliptic and equinox of date. The Sun's `sunPosition` path includes light-time
retardation and precession–nutation, but omits annual aberration and gravitational light deflection.
The apparent solar longitude includes corrections for the apparent place. Sharing an equinox origin
does not make these two solar longitudes equal at a given instant.

The Orloj marker therefore crosses a geometric $30^\circ$ divider at a different instant from an
apparent Dingqi term. The table is a coordinate comparison, not a prediction of displayed marker
crossing times. No fixed time offset is asserted, and the implementation is unchanged.

## 2. Ingresses, longitude midpoints, and cultural correspondences

The 12 **Zhongqi (中氣)** occur at $30^\circ k$, marking sign ingresses in the shared apparent
convention. The 12 **Jieling (節令)**, also called 節氣 in the narrow sense, occur at
$30^\circ k + 15^\circ$, the longitude midpoints of those signs.

```text
Apparent longitude:  0°             15°            30°            45°            60°
Tropical sign:      [----------- Aries -----------][---------- Taurus ----------]
Longitude position: Ingress        Midpoint       Ingress        Midpoint       Ingress
Term type:          中氣           節令           中氣           節令           中氣
Term name:          春分           清明           穀雨           立夏           小滿
```

The implemented gold dividers sample $30^\circ k$ and labels sample $30^\circ k+15^\circ$ through
`projection.eclipticPoint`. Those samples use the geometric dial convention. A longitude midpoint
is not generally the midpoint of the displayed arc: stereographic projection does not preserve
angular spacing along the offset ring. See [Projection](../orloj.md#projection).

### Historical 十二次 and the named tropical correspondence

Historical 十二次 describe stellar sectors through lunar-mansion boundaries. The
[*Han Shu*, 律曆志, 次度 passage][hanshu] records, for example:

> 降婁，初奎五度，雨水。中婁四度，春分。終於胃六度。

Here 春分 is associated with the **middle** of 降婁, not its beginning. Historical mansion degrees
and sector limits must not be read as modern equal $30^\circ$ tropical intervals. Dingqi defines
solar-term instants; it does not relocate these historical stellar boundaries.

The table below assigns the station names in sequence from 降婁/Aries to 娵訾/Pisces as a **named
tropical correspondence defined here**. Its station column is a nomenclature comparison, not a
historical boundary reconstruction or evidence of an exact alignment established by calendar reform.

### 月將 and the attributed 中氣 convention

The 中氣 switching rule is a Da Liu Ren convention attested in the *將星扶德* passage reproduced
in [*Gujin Tushu Jicheng*, volume 471, page 25][generals-rule]:

> 每月中氣後，太陽過宮，方為遇將。六壬月將加時，正此義也。

That passage says to use the general after the monthly 中氣 and solar palace crossing, and explicitly
connects this to 六壬. The linked transcription is marked unproofread; it is evidence for this
attributed rule, not a universal prescription across traditions or a modern numerical algorithm.

For the comparison table we apply that rule at the apparent 中氣 longitude, retaining the general
until the next 中氣. An em dash in a 節令 row means **no switch**, not no active general. Names and
branch associations follow the twelve palace gods in [*Liu Ren Zhi Nan*, volume 1][generals-names]
(登明/亥, 河魁/戌, 從魁/酉, 傳送/申, 小吉/未, 勝光/午, 太乙/巳, 天罡/辰, 太衝/卯,
功曹/寅, 大吉/丑, 神后/子). No claim of universality in 七政四餘 or other traditions is made.

The older [*Wu Jing Zong Yao*, 後集卷20, 六壬 passage][wujing] lists palace crossings **days after**
中氣 and uses stellar degrees. It illustrates why historical palace crossings cannot simply be
identified with the apparent tropical instants defined for this table.

## 3. Comparative reference table

All 24 longitude/name pairs follow [HKO][hko] and its [Chinese table][hko-chinese]. Sign ingress and midpoint columns are derived from
the shared apparent longitude definition. The station names use the tropical correspondence defined
above; the monthly-general column applies the attributed convention above. Seasonal names and the
four seasonal milestones use **Northern Hemisphere** terminology. They are not local weather forecasts.

| $\lambda_\odot$ | Type | Solar Term (節氣) | Pinyin / Translation | Tropical Zodiac | Longitude Offset | 十二次 names (tropical correspondence here) | 月將 at 中氣 (convention below) | Northern astronomical milestone |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| $0^\circ$ | 中氣 | 春分 | Chūnfēn (Vernal Equinox) | Aries (白羊宮 ♈) | Ingress ($0^\circ$) | 降婁 (Jiànglóu) | 河魁 (Hékuí) / 戌 | Vernal Equinox (Northern spring start) |
| $15^\circ$ | 節令 | 清明 | Qīngmíng (Pure Brightness) | Aries (白羊宮 ♈) | Midpoint ($15^\circ$) | 降婁 (Jiànglóu) | — | Mid-Aries |
| $30^\circ$ | 中氣 | 穀雨 | Gǔyǔ (Grain Rain) | Taurus (金牛宮 ♉) | Ingress ($0^\circ$) | 大梁 (Dàliáng) | 從魁 (Cóngkuí) / 酉 | — |
| $45^\circ$ | 節令 | 立夏 | Lìxià (Beginning of Summer) | Taurus (金牛宮 ♉) | Midpoint ($15^\circ$) | 大梁 (Dàliáng) | — | Mid-Taurus |
| $60^\circ$ | 中氣 | 小滿 | Xiǎomǎn (Grain Buds) | Gemini (雙子宮 ♊) | Ingress ($0^\circ$) | 實沈 (Shíshěn) | 傳送 (Chuánsòng) / 申 | — |
| $75^\circ$ | 節令 | 芒種 | Mángzhòng (Grain in Ear) | Gemini (雙子宮 ♊) | Midpoint ($15^\circ$) | 實沈 (Shíshěn) | — | Mid-Gemini |
| $90^\circ$ | 中氣 | 夏至 | Xiàzhì (Summer Solstice) | Cancer (巨蟹宮 ♋) | Ingress ($0^\circ$) | 鶉首 (Chúnshǒu) | 小吉 (Xiǎojí) / 未 | Summer Solstice (Northern summer start) |
| $105^\circ$ | 節令 | 小暑 | Xiǎoshǔ (Minor Heat) | Cancer (巨蟹宮 ♋) | Midpoint ($15^\circ$) | 鶉首 (Chúnshǒu) | — | Mid-Cancer |
| $120^\circ$ | 中氣 | 大暑 | Dàshǔ (Major Heat) | Leo (獅子宮 ♌) | Ingress ($0^\circ$) | 鶉火 (Chúnhuǒ) | 勝光 (Shèngguāng) / 午 | — |
| $135^\circ$ | 節令 | 立秋 | Lìqiū (Beginning of Autumn) | Leo (獅子宮 ♌) | Midpoint ($15^\circ$) | 鶉火 (Chúnhuǒ) | — | Mid-Leo |
| $150^\circ$ | 中氣 | 處暑 | Chùshǔ (End of Heat) | Virgo (室女宮 ♍) | Ingress ($0^\circ$) | 鶉尾 (Chúnwěi) | 太乙 (Tàiyǐ) / 巳 | — |
| $165^\circ$ | 節令 | 白露 | Báilù (White Dew) | Virgo (室女宮 ♍) | Midpoint ($15^\circ$) | 鶉尾 (Chúnwěi) | — | Mid-Virgo |
| $180^\circ$ | 中氣 | 秋分 | Qiūfēn (Autumnal Equinox) | Libra (天秤宮 ♎) | Ingress ($0^\circ$) | 壽星 (Shòuxīng) | 天罡 (Tiāngāng) / 辰 | Autumnal Equinox (Northern autumn start) |
| $195^\circ$ | 節令 | 寒露 | Hánlù (Cold Dew) | Libra (天秤宮 ♎) | Midpoint ($15^\circ$) | 壽星 (Shòuxīng) | — | Mid-Libra |
| $210^\circ$ | 中氣 | 霜降 | Shuāngjiàng (Frost's Descent) | Scorpio (天蠍宮 ♏) | Ingress ($0^\circ$) | 大火 (Dàhuǒ) | 太衝 (Tàichōng) / 卯 | — |
| $225^\circ$ | 節令 | 立冬 | Lìdōng (Beginning of Winter) | Scorpio (天蠍宮 ♏) | Midpoint ($15^\circ$) | 大火 (Dàhuǒ) | — | Mid-Scorpio |
| $240^\circ$ | 中氣 | 小雪 | Xiǎoxuě (Minor Snow) | Sagittarius (人馬宮 ♐) | Ingress ($0^\circ$) | 析木 (Xīmù) | 功曹 (Gōngcáo) / 寅 | — |
| $255^\circ$ | 節令 | 大雪 | Dàxuě (Major Snow) | Sagittarius (人馬宮 ♐) | Midpoint ($15^\circ$) | 析木 (Xīmù) | — | Mid-Sagittarius |
| $270^\circ$ | 中氣 | 冬至 | Dōngzhì (Winter Solstice) | Capricorn (摩羯宮 ♑) | Ingress ($0^\circ$) | 星紀 (Xīngjì) | 大吉 (Dàjí) / 丑 | Winter Solstice (Northern winter start) |
| $285^\circ$ | 節令 | 小寒 | Xiǎohán (Minor Cold) | Capricorn (摩羯宮 ♑) | Midpoint ($15^\circ$) | 星紀 (Xīngjì) | — | Mid-Capricorn |
| $300^\circ$ | 中氣 | 大寒 | Dàhán (Major Cold) | Aquarius (寶瓶宮 ♒) | Ingress ($0^\circ$) | 玄枵 (Xuánxiāo) | 神后 (Shénhòu) / 子 | — |
| $315^\circ$ | 節令 | 立春 | Lìchūn (Beginning of Spring) | Aquarius (寶瓶宮 ♒) | Midpoint ($15^\circ$) | 玄枵 (Xuánxiāo) | — | Mid-Aquarius |
| $330^\circ$ | 中氣 | 雨水 | Yǔshuǐ (Rain Water) | Pisces (雙魚宮 ♓) | Ingress ($0^\circ$) | 娵訾 (Jūzī) | 登明 (Dēngmíng) / 亥 | — |
| $345^\circ$ | 節令 | 驚蟄 | Jīngzhé (Awakening of Insects) | Pisces (雙魚宮 ♓) | Midpoint ($15^\circ$) | 娵訾 (Jūzī) | — | Mid-Pisces |

## 4. Hemisphere and projection conventions

### Direction on the implemented plates

The [projection contract](../orloj.md#projection) takes the projection from the pole above the
horizon: north for a northern site and south for a southern one. Canvas x increases rightward and
y downward. At fixed sidereal angle, increasing longitude runs **counter-clockwise on both plates**;
increasing sidereal angle rotates the sky **clockwise on both plates**.

The following cardinal examples assume **local apparent sidereal angle zero**, in both hemispheres:

| Longitude | Screen position |
| --- | --- |
| $0^\circ$ | Top ($x=0$, $y<0$) |
| $90^\circ$ | Left ($x<0$, $y=0$) |
| $180^\circ$ | Bottom ($x=0$, $y>0$) |
| $270^\circ$ | Right ($x>0$, $y=0$) |

On the northern plate Cancer is the outer boundary (radius 1) and Capricorn the inner tropic;
on the southern plate Capricorn is outer and Cancer inner. For equal absolute latitude,
obliquity, and sidereal angle, the tested relation is

$$\operatorname{south}(\lambda)=-\operatorname{north}(\lambda+180^\circ).$$

The longitude shift followed by point reflection preserves ordering; it does not reverse longitude
direction. This compares opposite longitudes, not point reflection of the same longitude. The ring
centre changes sides while the altitude shading depends only on absolute latitude. See
[`OrlojProjection`](../../app/src/main/kotlin/io/github/godaniya/astronomicalclockswallpaper/OrlojProjection.kt)
and `OrlojProjectionTest.southernRingIsPointReflected` for the implemented contract.

### Geocentric coordinates and local seasons

For a fixed coordinate convention, a geocentric longitude crossing has one instant globally.
Civil dates can differ by timezone. Changing observer hemisphere does not relabel the longitude
or reverse the sign sequence, but the local projection and seasonal interpretation differ.

HKO describes the terms' seasonal and agricultural origins. Their names reflect Northern Hemisphere
seasons and do not promise local temperature, rainfall, or daylight at every site. Dongzhi at
$270^\circ$ is the northern winter solstice and southern summer solstice; Xiazhi at $90^\circ$
has the opposite seasonal interpretation. Polar day/night and equatorial climates also limit
weather and daylight interpretations.

The 十二次 column retains cultural names as nomenclature. It makes no claim about astrological
validity, hemisphere-independent historical boundaries, or the visibility of an asterism at a site.

### Precession and the shared tropical convention

Tropical signs and Dingqi terms retain their relative longitude mapping **when both use the same
of-date tropical convention**: both are measured from the moving equinox. This does not make their
positions fixed relative to stars. Historical stellar stations and lunar mansions do not inherit
that invariance, nor does it remove the apparent/geometric distinction. No all-epoch numerical
accuracy claim is made; the engine's supported range and tested limits remain in
[Supported date range](calculations.md#supported-date-range).

[hko]: https://www.hko.gov.hk/en/gts/time/24solarterms.htm
[hko-chinese]: https://www.hko.gov.hk/tc/gts/time/24solarterms.htm
[hanshu]: https://zh.wikisource.org/wiki/漢書/卷021
[generals-rule]: https://zh.wikisource.org/wiki/Page:Gujin_Tushu_Jicheng,_Volume_471_(1700-1725).djvu/25#《將星扶德》
[generals-names]: https://zh.wikisource.org/wiki/六壬指南/1
[wujing]: https://zh.wikisource.org/wiki/武經總要_(四庫全書本)/後集卷20
