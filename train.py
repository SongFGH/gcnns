import torch
import torch.nn.functional as F
from torch.optim import Adam
from copy import deepcopy
from numpy import mean, std
from tqdm import tqdm

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')


class EarlyStopping:
    def __init__(self, patience, verbose, use_loss, use_acc, save_model):
        assert use_loss or use_acc, 'use loss or (and) acc'
        self.patience = patience
        self.use_loss = use_loss
        self.use_acc = use_acc
        self.save_model = save_model
        self.verbose = verbose
        self.counter = 0
        self.best_val_loss = float('inf')
        self.best_val_acc = 0
        self.state_dict = None

    def reset(self):
        self.counter = 0
        self.best_val_loss = float('inf')
        self.best_val_acc = 0
        self.state_dict = None

    def check(self, evals, model, epoch):
        if self.use_loss and self.use_acc:
            # For GAT, based on https://github.com/PetarV-/GAT/blob/master/execute_cora.py
            if evals['val_loss'] <= self.best_val_loss or evals['val_acc'] >= self.best_val_acc:
                if evals['val_loss'] <= self.best_val_loss and evals['val_acc'] >= self.best_val_acc:
                    if self.save_model:
                        self.state_dict = deepcopy(model.state_dict())
                self.best_val_loss = min(self.best_val_loss, evals['val_loss'])
                self.best_val_acc = max(self.best_val_acc, evals['val_acc'])
                self.counter = 0
            else:
                self.counter += 1
        elif self.use_loss:
            if evals['val_loss'] < self.best_val_loss:
                self.best_val_loss = evals['val_loss']
                self.counter = 0
                if self.save_model:
                    self.state_dict = deepcopy(model.state_dict())
            else:
                self.counter += 1
        elif self.use_acc:
            if evals['val_acc'] > self.best_val_acc:
                self.best_val_acc = evals['val_acc']
                self.counter = 0
                if self.save_model:
                    self.state_dict = deepcopy(model.state_dict())
            else:
                self.counter += 1
        stop = False
        if self.counter >= self.patience:
            stop = True
            if self.verbose:
                print("Stop training, epoch:", epoch)
            if self.save_model:
                model.load_state_dict(self.state_dict)
        return stop


class Trainer(object):
    def __init__(self, model, data, lr, weight_decay, epochs=200, niter=100, early_stopping=True, patience=10,
                 use_loss=True, use_acc=False, save_model=False, verbose=False):
        self.model = model
        self.optimizer = Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
        self.data = data
        self.lr = lr
        self.weight_decay = weight_decay
        self.epochs = epochs
        self.verbose = verbose
        self.niter = niter

        self.early_stopping = early_stopping
        if early_stopping:
            self.stop_checker = EarlyStopping(patience, verbose, use_loss, use_acc, save_model)

        self.data.to(device)

    def train(self):
        model, optimizer, data = self.model, self.optimizer, self.data
        model.train()
        optimizer.zero_grad()
        output = model(data)
        loss = F.nll_loss(output[data.train_mask], data.labels[data.train_mask])
        loss.backward()
        optimizer.step()

    def evaluate(self):
        model, data = self.model, self.data
        model.eval()

        with torch.no_grad():
            output = model(data)

        outputs = {}
        for key in ['train', 'val', 'test']:
            if key == 'train':
                mask = data.train_mask
            elif key == 'val':
                mask = data.val_mask
            else:
                mask = data.test_mask
            loss = F.nll_loss(output[mask], data.labels[mask]).item()
            pred = output[mask].max(dim=1)[1]
            acc = pred.eq(data.labels[mask]).sum().item() / mask.sum().item()

            outputs['{}_loss'.format(key)] = loss
            outputs['{}_acc'.format(key)] = acc

        return outputs

    def reset(self):
        self.optimizer = Adam(self.model.parameters(), lr=self.lr, weight_decay=self.weight_decay)
        self.model.to(device).reset_parameters()
        if self.early_stopping:
            self.stop_checker.reset()

    def run(self):
        val_acc_list = []
        test_acc_list = []

        for _ in tqdm(range(self.niter)):
            self.reset()
            if torch.cuda.is_available():
                torch.cuda.synchronize()

            for epoch in range(1, self.epochs + 1):
                self.train()
                evals = self.evaluate()

                if self.verbose:
                    print('epoch: {: 4d}'.format(epoch),
                          'train loss: {:.5f}'.format(evals['train_loss']),
                          'train acc: {:.5f}'.format(evals['train_acc']),
                          'val loss: {:.5f}'.format(evals['val_loss']),
                          'val acc: {:.5f}'.format(evals['val_acc']))

                if self.early_stopping:
                    if self.stop_checker.check(evals, self.model, epoch):
                        break

            if torch.cuda.is_available():
                torch.cuda.synchronize()

            evals = self.evaluate()
            if self.verbose:
                for met, val in evals.items():
                    print(met, val)

            val_acc_list.append(evals['val_acc'])
            test_acc_list.append(evals['test_acc'])

        print(mean(test_acc_list))
        print(std(test_acc_list))
        return {
            'val_acc': mean(val_acc_list),
            'test_acc': mean(test_acc_list),
            'test_acc_std': std(test_acc_list)
        }
