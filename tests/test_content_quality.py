from digital_company.content_quality import score_content
from digital_company.models import ContentPackage, FeaturedImageSpec


def package() -> ContentPackage:
    body = " ".join(["neisplaćena plata"] * 720)
    return ContentPackage(
        title="Neisplaćena plata u Republici Srpskoj",
        slug="neisplacena-plata-republika-srpska",
        focus_keyword="neisplaćena plata",
        secondary_keywords=["radno pravo"],
        seo_title="Neisplaćena plata: prava radnika u RS",
        meta_description=(
            "Neisplaćena plata zahtijeva brzu reakciju. Saznajte prava radnika, rokove i korake prema važećim propisima Republike Srpske."
        ),
        excerpt="Praktičan pregled prava radnika kada poslodavac duguje platu.",
        categories=["Radno pravo"],
        tags=["plata", "radni odnos"],
        html_content=f"<h1>Neisplaćena plata</h1><p>{body}</p><h2>Šta uraditi</h2><p>Koraci.</p>",
        internal_links=["/usluge/radno-pravo"],
        cta="Kontaktirajte advokata radi procjene slučaja.",
        jurisdiction_notes="Republika Srpska; pravna provjera prije objave.",
        source_urls=["https://example.com/law", "https://example.com/court"],
        featured_image=FeaturedImageSpec(
            prompt="Professional editorial photograph about employment law in Banja Luka.",
            alt_text="Radnik pregledava obračun neisplaćene plate",
            filename="neisplacena-plata",
        ),
    )


def test_complete_package_passes_explainable_quality_gate():
    report = score_content(package(), 700)
    assert report["name"] == "Telekt SEO QA"
    assert report["score"] >= 70
    assert report["word_count"] >= 700
