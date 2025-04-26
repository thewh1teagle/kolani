"""
Train from scratch:
    uv run src/train.py --device cpu --epochs 1
Train from checkpoint:
    uv run src/train.py --device cpu --epochs 1 --model_checkpoint ckpt/step_6_loss_0.4250 --pre_training_step 6
"""

from argparse import ArgumentParser
from transformers import AutoTokenizer
from glob import glob
import os
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm, trange
import torch
from torch import nn
from torch.nn.utils.rnn import pad_sequence
from model import PhoNikudModel, STRESS_CHAR, MOBILE_SHVA_CHAR

def get_opts():
    parser = ArgumentParser()
    parser.add_argument('-m', '--model_checkpoint',
                        default='dicta-il/dictabert-large-char-menaked', type=str)
    parser.add_argument('-d', '--device',
                        default='cuda', type=str)
    parser.add_argument('-dd', '--data_dir',
                        default='data/', type=str)
    parser.add_argument('-o', '--output_dir',
                        default='ckpt', type=str)
    parser.add_argument('--batch_size', default=4, type=int)
    parser.add_argument('--epochs', default=10, type=int)
    parser.add_argument('--pre_training_step', default=0, type=int)
    parser.add_argument('--learning_rate', default=1e-3, type=float)
    parser.add_argument('--num_workers', default=0, type=int)

    return parser.parse_args()

class AnnotatedLine:
    
    def __init__(self, raw_text):
        self.text = "" # will contain plain hebrew text
        stress = [] # will contain 0/1 for each character (1=stressed)
        mobile_shva = [] # will contain 0/1 for each caracter (1=mobile shva)
        for char in raw_text:
            if char == STRESS_CHAR:
                stress[-1] = 1
            elif char == MOBILE_SHVA_CHAR:
                mobile_shva[-1] = 1
            else:
                self.text += char
                stress += [0]
                mobile_shva += [0]
        assert len(self.text) == len(stress) == len(mobile_shva)
        stress_tensor = torch.tensor(stress)
        mobile_shva_tensor = torch.tensor(mobile_shva)

        self.target = torch.stack((stress_tensor, mobile_shva_tensor))
        # ^ shape: (n_chars, 2)

class TrainData(Dataset):

    def __init__(self, args):
        
        self.max_context_length = 2048
        

        files = glob(os.path.join(args.data_dir, "train", "*.txt"))
        print(len(files), "text files found; using them for training data...")
        self.lines = self._load_lines(files)

    def _load_lines(self, files: list[str]):
        lines = []
        for file in files:
            with open(file, "r", encoding='utf-8') as fp:
                for line in fp:
                    # While the line is longer than max_context_length, split it into chunks
                    while len(line) > self.max_context_length:
                        lines.append(line[:self.max_context_length].strip())  # Add the first chunk
                        line = line[self.max_context_length:]  # Keep the remainder of the line
                    
                    # Add the remaining part of the line if it fits within the max_context_length
                    if line.strip():
                        lines.append(line.strip())
        return lines

    def __len__(self):
        return len(self.lines)
    
    def __getitem__(self, idx):
        text = self.lines[idx]
        return AnnotatedLine(text)


class Collator:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def collate_fn(self, items):
        inputs = self.tokenizer(
            [x.text for x in items],
            padding=True,
            truncation=True,
            return_tensors='pt')
        targets = pad_sequence([x.target.T for x in items], batch_first=True)
        # ^ shape: (batch_size, n_chars_padded, 2)

        return inputs, targets


def main():
    args = get_opts()

    print("Loading model...")

    model = PhoNikudModel.from_pretrained(args.model_checkpoint, trust_remote_code=True)
    model.to(args.device)
    model.freeze_base_model()
    # ^ we will only train extra layers

    tokenizer = AutoTokenizer.from_pretrained(args.model_checkpoint)
    collator = Collator(tokenizer)

    print("Loading data...")
    data = TrainData(args)

    print("Training...")

    dl = DataLoader(data,
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collator.collate_fn,
        num_workers=args.num_workers
    )

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    criterion = nn.BCEWithLogitsLoss()

    step = 0 + args.pre_training_step
    for _ in trange(args.epochs, desc="Epoch"):
        pbar = tqdm(dl, desc="Train iter")
        for inputs, targets in pbar:

            optimizer.zero_grad()

            inputs = inputs.to(args.device)
            targets = targets.to(args.device)
            # ^ shape: (batch_size, n_chars_padded, 2)
            output = model(inputs)
            # ^ shape: (batch_size, n_chars_padded, 2)
            additional_logits = output.additional_logits

            loss = criterion(
                additional_logits[:, 1:-1], # skip BOS and EOS symbols
                targets.float())
            # ^ NOTE: loss is only on new labels (stress, mobile shva)
            # rest of network is frozen so nikkud predictions should not change

            loss.backward()
            optimizer.step()

            pbar.set_description(f"Train iter (L={loss.item():.4f})")
            step += 1
    
    epoch_loss = loss.item()
    save_dir = f'{args.output_dir}/step_{step+1}_loss_{epoch_loss:.4f}'
    print("Saving trained model to:", save_dir)
    model.save_pretrained(save_dir)
    tokenizer.save_pretrained(save_dir)
    print("Model saved.")

    print("Testing...")

    model.eval()

    test_fn = os.path.join(args.data_dir, "test.txt")
    with open(test_fn, "r", encoding='utf-8') as f:
        test_text = f.read().strip()

    for line in test_text.splitlines():
        if not line.strip():
            continue
        print(line)
        print(model.predict([line], tokenizer, mark_matres_lectionis='*'))
        print()


if __name__ == "__main__":
    main()
