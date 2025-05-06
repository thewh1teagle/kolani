import torch
from torch import nn
from dataclasses import dataclass
from transformers.utils import ModelOutput
from .base_model import (
    BertForDiacritization,
    remove_nikkud,
    is_hebrew_letter,
    is_matres_letter,
)

STRESS_CHAR = "\u05ab"  # "ole" symbol marks stress
MOBILE_SHVA_CHAR = "\u05bd"  # "meteg" symbol marks shva na (mobile shva)
PREFIX_CHAR = "|"  # vertical bar


@dataclass
class MenakedLogitsOutput(ModelOutput):
    nikud_logits: torch.FloatTensor = None
    shin_logits: torch.FloatTensor = None
    additional_logits: torch.FloatTensor = None  # For stress, mobile shva, and prefix

    def detach(self):
        return MenakedLogitsOutput(
            self.nikud_logits.detach(),
            self.shin_logits.detach(),
            self.additional_logits.detach(),
        )


class PhoNikudModel(BertForDiacritization):
    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self.mlp = nn.Sequential(nn.Linear(1024, 100), nn.ReLU(), nn.Linear(100, 3))
        # ^ predicts stress, mobile shva, and prefix; outputs are logits

    def freeze_mlp_components(self, indices: list[int]):
        final_layer = self.mlp[2]
        with torch.no_grad():
            for idx in indices:
                final_layer.weight[idx].requires_grad = False
                final_layer.bias[idx].requires_grad = False

    def freeze_base_model(self):
        self.bert.eval()
        self.menaked.eval()
        for param in self.bert.parameters():
            param.requires_grad = False

        for param in self.menaked.parameters():
            param.requires_grad = False

    def forward(self, x):
        # based on: https://huggingface.co/dicta-il/dictabert-large-char-menaked/blob/main/BertForDiacritization.py
        bert_outputs = self.bert(**x)
        hidden_states = bert_outputs.last_hidden_state
        # ^ shape: (batch_size, n_chars_padded, 1024)
        hidden_states = self.dropout(hidden_states)

        _, nikud_logits = self.menaked(hidden_states)
        # ^ nikud_logits: MenakedLogitsOutput

        additional_logits = self.mlp(hidden_states)
        # ^ shape: (batch_size, n_chars_padded, 3) [stress, mobile shva, and prefix]

        return MenakedLogitsOutput(
            nikud_logits.nikud_logits, nikud_logits.shin_logits, additional_logits
        )

    @torch.no_grad()
    def predict(
        self, sentences, tokenizer, mark_matres_lectionis=None, padding="longest"
    ):
        # based on: https://huggingface.co/dicta-il/dictabert-large-char-menaked/blob/main/BertForDiacritization.py

        sentences = [remove_nikkud(sentence) for sentence in sentences]
        # assert the lengths aren't out of range
        assert all(
            len(sentence) + 2 <= tokenizer.model_max_length for sentence in sentences
        ), (
            f"All sentences must be <= {tokenizer.model_max_length}, please segment and try again"
        )

        # tokenize the inputs and convert them to relevant device
        inputs = tokenizer(
            sentences,
            padding=padding,
            truncation=True,
            return_tensors="pt",
            return_offsets_mapping=True,
        )
        offset_mapping = inputs.pop("offset_mapping")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        # calculate the predictions
        output = self.forward(inputs)
        nikud_logits = output.detach()
        additional_logits = output.additional_logits.detach()
        nikud_predictions = nikud_logits.nikud_logits.argmax(dim=-1).tolist()
        shin_predictions = nikud_logits.shin_logits.argmax(dim=-1).tolist()

        stress_predictions = (additional_logits[..., 0] > 0).int().tolist()
        mobile_shva_predictions = (additional_logits[..., 1] > 0).int().tolist()
        prefix_predictions = (additional_logits[..., 2] > 0).int().tolist()

        ret = []
        for sent_idx, (sentence, sent_offsets) in enumerate(
            zip(sentences, offset_mapping)
        ):
            # assign the nikud to each letter!
            output = []
            prev_index = 0
            for idx, offsets in enumerate(sent_offsets):
                # add in anything we missed
                if offsets[0] > prev_index:
                    output.append(sentence[prev_index : offsets[0]])
                if offsets[1] - offsets[0] != 1:
                    continue

                # get our next char
                char = sentence[offsets[0] : offsets[1]]
                prev_index = offsets[1]
                if not is_hebrew_letter(char):
                    output.append(char)
                    continue

                nikud = self.config.nikud_classes[nikud_predictions[sent_idx][idx]]
                shin = (
                    ""
                    if char != "ש"
                    else self.config.shin_classes[shin_predictions[sent_idx][idx]]
                )

                # check for matres lectionis
                if nikud == self.config.mat_lect_token:
                    if not is_matres_letter(char):
                        nikud = ""  # don't allow matres on irrelevant letters
                    elif mark_matres_lectionis is not None:
                        nikud = mark_matres_lectionis
                    else:
                        continue

                stress = STRESS_CHAR if stress_predictions[sent_idx][idx] == 1 else ""
                mobile_shva = (
                    MOBILE_SHVA_CHAR
                    if mobile_shva_predictions[sent_idx][idx] == 1
                    else ""
                )
                prefix = PREFIX_CHAR if prefix_predictions[sent_idx][idx] == 1 else ""

                output.append(char + shin + nikud + prefix + stress + mobile_shva)
            output.append(sentence[prev_index:])
            ret.append("".join(output))

        return ret
