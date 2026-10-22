# Empirical OpenAlex Disparity Analysis: Submission vs. Canonical Work Level

Comparative evaluation of discrepancies between the Australian Excellence in Research for Australia (ERA) dataset and matched OpenAlex records across two analytical perspectives:
  * **Perspective 1: Submission-Level** ($N = 536,707$ institutional submissions): Evaluates each university's submitted metadata individually against OpenAlex.
  * **Perspective 2: Canonical Work-Level** ($N = 427,179$ distinct intellectual works): Consolidates multi-HEP duplicate submissions into a single canonical ERA record before comparison (Olensky's hand-coding methodology).

---

## 1. Title Disparities Comparison

| Disparity Category | Olensky Code | Submission Level ($N=536,707$) | Sub. Rate (%) | Canonical Work Level ($N=427,179$) | Canon. Rate (%) |
| :--- | :---: | ---: | ---: | ---: | ---: |
| **Exact Title Match** | — | 265,916 | 49.5% | 214,481 | 50.2% |
| **Punctuation & Casing** | **R** | 225,922 | 42.1% | 177,091 | 41.5% |
| **Cropped Subtitle** | **F** | 6,905 | 1.3% | 5,703 | 1.3% |
| **Typographical / Variant** | **A** | 19,793 | 3.7% | 15,378 | 3.6% |
| **Spelling Error / Indel** | **B** | 15,714 | 2.9% | 12,312 | 2.9% |
| **Markup / HTML Entities** | **Q** | 94 | 0.0% | 70 | 0.0% |
| **Discordant / Mismatch** | **D** | 2,335 | 0.4% | 2,117 | 0.5% |

---

## 2. Publication Year Disparities (Olensky Code T)

| Year Alignment | Submission Level ($N=536,707$) | Sub. Rate (%) | Canonical Work Level ($N=427,179$) | Canon. Rate (%) |
| :--- | ---: | ---: | ---: | ---: |
| **Exact Year Agreement** | 420,712 | 78.4% | 336,154 | 78.7% |
| **Off by 1 Year ($\pm 1$)** | 103,793 | 19.3% | 80,450 | 18.8% |
| **Off by 2+ Years ($\ge 2$)** | 12,073 | 2.2% | 10,447 | 2.4% |

---

## 3. Identifier (DOI) Disparities

| DOI Status | Submission Level ($N=536,707$) | Sub. Rate (%) | Canonical Work Level ($N=427,179$) | Canon. Rate (%) |
| :--- | ---: | ---: | ---: | ---: |
| **Exact Matching DOI** | 374,069 | 69.7% | 299,688 | 70.2% |
| **DOI Omission (Code E)** | 108,616 | 20.2% | 77,731 | 18.2% |
| **DOI Conflict (Code D)** | 1,721 | 0.3% | 1,458 | 0.3% |

---

## 4. Institutional Disparity Divergence across Co-Submitting HEPs

Evaluated across the **82,813 multi-HEP works** co-submitted by two or more Australian universities:

* **Divergent Title Classifications**: **32,489 works (39.2%)** receive conflicting disparity ratings against OpenAlex depending on which university's submission is evaluated (e.g. University A matches exactly, while University B is flagged with Code R or Code F).
* **Divergent DOI Classifications**: **25,598 works (30.9%)** have conflicting DOI presence across submitting universities (one university provides the DOI while another omits it).
* **Divergent Year Classifications**: **2,257 works (2.7%)** have differing reference years reported across universities for the same publication.

---

## 5. Disparity Profiles & Coupled Lifecycle Drift Analysis

| Disparity Profile | Description | Submission Level ($N=536,707$) | Sub. Rate (%) | Canonical Work Level ($N=427,179$) | Canon. Rate (%) |
| :--- | :--- | ---: | ---: | ---: | ---: |
| **Clean (No Disparities)** | Exact title, year, and DOI agreement | 168,875 | 31.5% | 141,361 | 33.1% |
| **Isolated Title Disparity** | Title varies, year and DOI match | 167,703 | 31.2% | 134,668 | 31.5% |
| **Isolated Year Disparity** | Publication year off, title and DOI match | 45,060 | 8.4% | 36,599 | 8.6% |
| **Isolated DOI Disparity** | DOI omitted/conflicted, title and year match | 39,116 | 7.3% | 27,296 | 6.4% |
| **Coupled Lifecycle Drift** | Year and DOI diverge, title matches exactly | 12,865 | 2.4% | 9,225 | 2.2% |
| **Coupled Drift with Title Var.** | Year, DOI, and title all diverge | 13,209 | 2.5% | 9,711 | 2.3% |
| **Title & Year Disparity** | Title and year diverge, DOI matches | 44,732 | 8.3% | 35,362 | 8.3% |
| **Title & DOI Disparity** | Title and DOI diverge, year matches | 45,147 | 8.4% | 32,957 | 7.7% |
| **Total Coupled Lifecycle Drift** | All records where both year and DOI diverge ($Year > 0 \land DOI \ne Match$) | 26,074 | 4.9% | 18,936 | 4.4% |
