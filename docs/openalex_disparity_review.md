# Empirical OpenAlex Disparity Analysis: Submission vs. Canonical Work Level

Comparative evaluation of discrepancies between the Australian Excellence in Research for Australia (ERA) dataset and matched OpenAlex records across two analytical perspectives:
  * **Perspective 1: Submission-Level** ($N = 540,353$ institutional submissions): Evaluates each university's submitted metadata individually against OpenAlex.
  * **Perspective 2: Canonical Work-Level** ($N = 431,842$ distinct intellectual works): Consolidates multi-HEP duplicate submissions into a single canonical ERA record before comparison (Olensky's hand-coding methodology).

---

## 1. Title Disparities Comparison

| Disparity Category | Olensky Code | Submission Level ($N=540,353$) | Sub. Rate (%) | Canonical Work Level ($N=431,842$) | Canon. Rate (%) |
| :--- | :---: | ---: | ---: | ---: | ---: |
| **Exact Title Match** | — | 264,489 | 48.9% | 213,752 | 49.5% |
| **Punctuation & Casing** | **R** | 219,224 | 40.6% | 173,023 | 40.1% |
| **Cropped Subtitle** | **F** | 6,740 | 1.2% | 5,579 | 1.3% |
| **Typographical / Variant** | **A** | 33,372 | 6.2% | 26,208 | 6.1% |
| **Spelling Error / Indel** | **B** | 10,252 | 1.9% | 8,201 | 1.9% |
| **Markup / HTML Entities** | **Q** | 233 | 0.0% | 182 | 0.0% |
| **Discordant / Mismatch** | **D** | 6,014 | 1.1% | 4,869 | 1.1% |

---

## 2. Publication Year Disparities (Olensky Code T)

| Year Alignment | Submission Level ($N=540,353$) | Sub. Rate (%) | Canonical Work Level ($N=431,842$) | Canon. Rate (%) |
| :--- | ---: | ---: | ---: | ---: |
| **Exact Year Agreement** | 421,992 | 78.1% | 338,851 | 78.5% |
| **Off by 1 Year ($\pm 1$)** | 103,056 | 19.1% | 80,120 | 18.6% |
| **Off by 2+ Years ($\ge 2$)** | 15,153 | 2.8% | 12,723 | 2.9% |

---

## 3. Identifier (DOI) Disparities

| DOI Status | Submission Level ($N=540,353$) | Sub. Rate (%) | Canonical Work Level ($N=431,842$) | Canon. Rate (%) |
| :--- | ---: | ---: | ---: | ---: |
| **Exact Matching DOI** | 374,413 | 69.3% | 299,990 | 69.5% |
| **DOI Omission (Code E)** | 106,721 | 19.8% | 76,988 | 17.8% |
| **DOI Conflict (Code D)** | 1,369 | 0.3% | 1,170 | 0.3% |

---

## 4. Institutional Disparity Divergence across Co-Submitting HEPs

Evaluated across the **82,196 multi-HEP works** co-submitted by two or more Australian universities:

* **Divergent Title Classifications**: **30,972 works (37.7%)** receive conflicting disparity ratings against OpenAlex depending on which university's submission is evaluated (e.g. University A matches exactly, while University B is flagged with Code R or Code F).
* **Divergent DOI Classifications**: **24,480 works (29.8%)** have conflicting DOI presence across submitting universities (one university provides the DOI while another omits it).
* **Divergent Year Classifications**: **2,245 works (2.7%)** have differing reference years reported across universities for the same publication.
