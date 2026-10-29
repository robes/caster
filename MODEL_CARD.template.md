# Model Card: {{ project_name }} {{ version }}

{% raw %}<!--
A model card (Mitchell et al., 2019) — the human-facing companion to the
machine-verifiable provenance Caster records.

This is the template; it is updated with Caster, so don't edit it. Copy it to
MODEL_CARD.md, or to MODEL_CARD.<model_id>.md for each model when the project
releases several (<model_id> as in the release config), and author the judgment
sections there as you work. At release time `caster.package` renders that card
into the bundle,
filling the factual fields (the `{{ ... }}` and loops below) from the run-stamp,
the held-out eval, the release config, and the data_sources records — and
carrying your authored prose through unchanged.

The `{{ ... }}` / `{% ... %}` markers are Jinja: leave them, they fill
themselves. The sections marked "REQUIRED" are yours — they cannot be
auto-filled, and a card that ships with them blank is incomplete even though the
facts will look done.
-->{% endraw %}

## Model details

- **Name:** {{ project_name }}
- **Version:** {{ version }}
- **Release identifier:** {{ model_id }}
{% if organization %}
- **Organization:** {{ organization }}
{% endif %}
{% if contact_email %}
- **Contact:** {{ contact_email }}
{% endif %}
{% if license %}
- **License:** {{ license }}
{% endif %}
- **Source commit:** {{ commit or "(none recorded — see provenance/run)" }}
- **Date packaged:** {{ date_packaged }}
- **Description:** {{ description }}
- **Model type / architecture:** <!-- REQUIRED: what the model *is* (Caster records the commit and environment, not this) -->
- **Citation / paper:** <!-- if any -->

## Intended use

<!-- REQUIRED: the primary intended uses and users — be specific about what this model is *for*. -->

<!-- REQUIRED: out-of-scope and prohibited uses — where this model should *not* be applied. -->

## Training data

{% if datasets %}
This release uses the following data source(s); their records travel in the
bundle under `provenance/data_sources/`:
{% for d in datasets %}
- **{{ d.name }}** — {% if d.disposition == "embed" %}included under `datasets/{{ d.name }}/`{% if d.identifier %}; published at <{{ d.identifier }}>{% endif %}{% elif d.identifier %}not included; obtain it from <{{ d.identifier }}>{% else %}not included; see its record for how it was produced{% endif %}{% if d.citation_doi %}. Cite <https://doi.org/{{ d.citation_doi }}>{% endif %}.
{% endfor %}
{% else %}
<!-- REQUIRED: describe the training data and link its record. -->
{% endif %}

<!-- REQUIRED: how the data was split, preprocessing of note, and known
characteristics or gaps. The split sizes/fractions are recorded in
provenance/run/; their *meaning* is yours to explain. -->

## Evaluation

{% if metrics %}
Reported metrics (from the recorded run{% if eval_name %}, held-out: `{{ eval_name }}`{% endif %}):

| metric | value |
|---|---|
{% for k, v in metrics.items() %}| {{ k }} | {{ v }} |
{% endfor %}
{% else %}
<!-- No metrics were found in the eval or run-stamp; add the evaluation results. -->
{% endif %}

<!-- REQUIRED: the evaluation protocol — what these numbers mean, the test set,
and any metric the headline numbers do *not* capture. -->

## Limitations, risks, and ethical considerations

<!-- REQUIRED: known limitations, failure modes, and biases. -->

<!-- REQUIRED: ethical considerations and any risks of deployment. -->

## Provenance

The verifiable record — exact commit, environment, data integrity manifest, and
source archive — travels alongside this card in the release bundle. See
`provenance/` (the run-stamp, config, eval, and data-source records) and the
RO-Crate metadata at the bundle root.
