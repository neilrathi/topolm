import torch
import hydra
import logging
import time
import matplotlib.pyplot as plt
import cramming
# import evaluate
from tqdm import tqdm
import pickle as pkl
from glob import glob
import os
import pandas as pd
import numpy as np
from transformers import AutoTokenizer

from collections import defaultdict
from torch.utils.data import Dataset, DataLoader

log = logging.getLogger(__name__)

MODEL_FILE = '~/projects/topo-eval/outputs/topotest/checkpoints/ScriptableMaskedLM_2024-07-03_9.9417/model.pth'
MASK_FILE = '~/projects/topo-eval/dumps/topotest-lmask.pkl'
SAVEPATH = '~/projects/topo-eval/dumps/topotest-responses.pkl'
STIMULI_FILE = '~/projects/topo-eval/fedorenko_response_stimuli/responses.csv'

activations = defaultdict(list)

class Fed10_ResponseDataset(Dataset):
    def __init__(self, is_pretrained):
        data = pd.read_csv(os.path.expanduser(STIMULI_FILE))
        vocab = set(' '.join(data['sentence']).split())

        self.is_pretrained = is_pretrained

        self.vocab = sorted(list(vocab))
        self.w2idx = {w: i for i, w in enumerate(self.vocab)}
        self.idx2w = {i: w for i, w in enumerate(self.vocab)}

        items = list(zip(data['sentence'], data['condition']))
        self.items = sorted(items, key = lambda x: x[1])

        # self.sentences = data[data["stim14"]=="S"]["sent"]
        # self.non_words = data[data["stim14"]=="N"]["sent"]

    def tokenize(self, sent):
        return torch.tensor([self.w2idx[w]+20_000 for w in sent.split()])

    def __getitem__(self, idx):
        if self.is_pretrained:
            return self.items[idx][0].strip(), self.items[idx][1]
        else:
            return self.tokenize(self.items[idx][0].strip()), self.items[idx][1]

    def __len__(self):
        return len(self.items)
    
    def vocab_size(self):
        return len(self.vocab) + 20_000

def hook_fn(layer_name, module, inp, out):
    activations[layer_name].append(out.squeeze(1).mean(dim = 0).detach().cpu())

def _register_hook(model, layer_name):
    for name, layer in model.named_modules():
        if name == layer_name:
            return layer.register_forward_hook(lambda module, inp, out: hook_fn(layer_name, module, inp, out))

@torch.no_grad()
def main_process(cfg, setup):

    with open(os.path.expanduser(MASK_FILE), 'rb') as f:
        layer_mask = pkl.load(f)

    all_conditions = ['W', 'S', 'J', 'N']
    layer_names = [f'encoder.layers.{i}.attn.dense' for i in range(16)]

    final_responses = {
        condition : [] for condition in all_conditions
    }

    cfg.impl['microbatch_size'] = 4

    tokenizer = AutoTokenizer.from_pretrained("JonasGeiping/crammed-bert")

    dataset = Fed10_ResponseDataset(tokenizer)
    dataloader = DataLoader(dataset, batch_size=1)

    model = cramming.construct_model(cfg.arch, tokenizer.vocab_size)
    
    model_engine, _, _, _ = cramming.load_backend(model, None, tokenizer, cfg.train, cfg.impl, setup=setup)
    model_path = os.path.expanduser(MODEL_FILE)
    model_engine.load_checkpoint(cfg.arch, model_path)

    model_engine.eval()

    for i in range(16):
        print(f'Evaluating layer {layer_names[i]}...')
        hook = _register_hook(model, layer_names[i])

        for batch_idx, batch_data in tqdm(enumerate(dataloader), total=len(dataloader)):
            sent, input_type = batch_data
            tokens = tokenizer(sent, truncation=True, max_length=12, return_attention_mask = False, return_tensors='pt')

            _, _ = model_engine.forward_inference(**tokens)

        hook.remove()

    for i in range(len(activations[layer_names[0]])):

        condition = all_conditions[i // 160]

        tot_activations = np.array([activations[layer][i] for layer in activations])

        m = (tot_activations * layer_mask).mean()
        final_responses[condition].append(m)

    with open(os.path.expanduser(SAVEPATH), 'wb') as f:
        pkl.dump(final_responses, f)

@hydra.main(config_path="cramming/config", config_name="cfg_pretrain", version_base="1.1")
def launch(cfg):
    cramming.utils.main_launcher(cfg, main_process, job_name="pretraining")

if __name__ == "__main__":
    launch()
