# Terms of use

This data is for learning and practice at the Houston AI Institute Hackathon 2026. The event rules also apply. Where they cover the same thing, the event rules win.

**What it is.** Real US offshore incident reports, published by the US Bureau of Safety and Environmental Enforcement (BSEE), mixed with generated values. Every cell says which it is. The three companies (Alder, Hazel and Hornbeam) are fictional. This project makes no claim about any real operator or person, and nothing here is safety advice.

**People in the reports.** Some reports describe real people who were hurt. Names were removed where a pattern could find them, but some may remain. Do not try to identify, look up or contact anyone in the reports. If you spot a name, tell a mentor or an organiser, and leave it out of anything you show, submit or put in a repository.

**Company names.** In the fictional company tables, real operator names are replaced with `[company]` where a pattern finds them. Do not try to work out which real company is meant.

**Licence.** The tables mix content under different terms. The provenance files say which cell is which.

- Report text and BSEE values (cells marked `src` or `mod`, and columns starting `src_` or `bsee_`) come from reports published by BSEE, a US government agency. This project treats them as public domain. Please credit them as: "Source: Bureau of Safety and Environmental Enforcement, US Department of the Interior."
- What this project added (the generated, crosswalked and pseudonym values, the fictional companies, the provenance files, the schema files and the code) is released under the MIT License. The full text is in `LICENSE`. You may copy, change and include it in your project, including a public repository, as long as `LICENSE` goes with the copy.
- The column names and category values follow an Element 19 investigation workbook and are published with its provider's permission. You may use them in your project, including a public repository.
- The PSM Framework element names (in comments in `advanced/schema/crosswalk.yaml`) belong to the Energy Institute. If you use one, credit the Energy Institute.

**Your project.** Who owns what you make, and how it is released, is set by the event rules, not by these terms. If you put it in a public repository, link to this bundle instead of uploading its data files, because real names may remain in the report text. Put the BSEE credit in your README.

**No warranty.** The data has known defects (see `README_FIRST.md`). It is provided as is, for practice.

**Problems.** If something in the data upsets you, looks wrong or seems unsafe to share, stop and tell a mentor or an organiser.
