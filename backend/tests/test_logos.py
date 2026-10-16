import numpy as np

from nyra import logos


def test_the_stored_description_embeddings_match_the_prompts_and_the_model():
    text = logos._text()  # raises when the file is out of date: regenerate it with `python -m nyra.logos`
    assert text.shape == (len(logos.LOGO_PROMPTS) + len(logos.OTHER_PROMPTS), 512)
    assert np.allclose(np.linalg.norm(text, axis=1), 1.0, atol=1e-4)


def test_a_logo_description_scores_as_a_logo_and_a_photo_description_does_not():
    text = logos._text()
    logo_like = text[1:2]  # "a brand logo on a plain background"
    photo_like = text[len(logos.LOGO_PROMPTS) : len(logos.LOGO_PROMPTS) + 1]  # "a photograph of a product"
    assert logos.is_logo(logo_like).tolist() == [True]
    assert logos.is_logo(photo_like).tolist() == [False]
    assert logos.logo_probability(photo_like)[0] < 0.1


def test_nothing_in_nothing_out_and_probabilities_stay_between_zero_and_one():
    """Real embeddings are what the score means something for; this only guards the shape and the range."""
    assert logos.logo_probability(np.zeros((0, 512), dtype=np.float32)).shape == (0,)
    rng = np.random.default_rng(3)
    random = rng.normal(size=(50, 512)).astype(np.float32)
    random /= np.linalg.norm(random, axis=1, keepdims=True)
    probabilities = logos.logo_probability(random)  # meaningless for random directions, but always a probability
    assert ((probabilities >= 0) & (probabilities <= 1)).all()
