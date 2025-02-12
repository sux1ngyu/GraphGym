import logging
import time
import os

import torch
from tqdm import tqdm


from graphgym.checkpoint import clean_ckpt, load_ckpt, save_ckpt
from graphgym.config import cfg
from graphgym.loss import compute_loss
from graphgym.utils.epoch import is_ckpt_epoch, is_eval_epoch


def train_epoch(logger, loader, model, optimizer, scheduler):
    model.train()
    time_start = time.time()
    for batch in loader:
        batch.split = 'train'
        optimizer.zero_grad()
        batch.to(torch.device(cfg.device))
        pred, true, last_hidden = model(batch)
        # print(f"pred shape: {pred.shape}. last hidden shape: {last_hidden.shape}")
        loss, pred_score = compute_loss(pred, true)
        loss.backward()
        optimizer.step()
        logger.update_stats(true=true.detach().cpu(),
                            pred=pred_score.detach().cpu(),
                            loss=loss.item(),
                            lr=scheduler.get_last_lr()[0],
                            time_used=time.time() - time_start,
                            params=cfg.params)
        time_start = time.time()
    scheduler.step()


@torch.no_grad()
def eval_epoch(logger, loader, model, split='val'):
    model.eval()
    time_start = time.time()
    for batch in loader:
        batch.split = split
        batch.to(torch.device(cfg.device))
        pred, true, _ = model(batch)
        loss, pred_score = compute_loss(pred, true)
        logger.update_stats(true=true.detach().cpu(),
                            pred=pred_score.detach().cpu(),
                            loss=loss.item(),
                            lr=0,
                            time_used=time.time() - time_start,
                            params=cfg.params)
        time_start = time.time()


def train(loggers, loaders, model, optimizer, scheduler):
    r"""
    The core training pipeline

    Args:
        loggers: List of loggers
        loaders: List of loaders
        model: GNN model
        optimizer: PyTorch optimizer
        scheduler: PyTorch learning rate scheduler

    """
    start_epoch = 0
    if cfg.train.auto_resume:
        start_epoch = load_ckpt(model, optimizer, scheduler)
    if start_epoch == cfg.optim.max_epoch:
        logging.info('Checkpoint found, Task already done')
    else:
        logging.info('Start from epoch {}'.format(start_epoch))

    num_splits = len(loggers)
    split_names = ['val', 'test']
    for cur_epoch in range(start_epoch, cfg.optim.max_epoch):
        train_epoch(loggers[0], loaders[0], model, optimizer, scheduler)
        loggers[0].write_epoch(cur_epoch)
        if is_eval_epoch(cur_epoch):
            for i in range(1, num_splits):
                eval_epoch(loggers[i], loaders[i], model,
                           split=split_names[i - 1])
                loggers[i].write_epoch(cur_epoch)
        if is_ckpt_epoch(cur_epoch):
            save_ckpt(model, optimizer, scheduler, cur_epoch)
    for logger in loggers:
        logger.close()
    if cfg.train.ckpt_clean:
        clean_ckpt()

    logging.info('Task done, results saved in {}'.format(cfg.out_dir))




def train_nas(loggers, loaders, model, optimizer, scheduler, args, model_dict_len, metric='auc'):
    start_epoch = 0
    if cfg.train.auto_resume:
        start_epoch = load_ckpt(model, optimizer, scheduler)
    if start_epoch == cfg.optim.max_epoch:
        logging.info('Checkpoint found, Task already done')
    else:
        logging.info('Start from epoch {}'.format(start_epoch))

    num_params = sum(p.numel() for p in model.parameters() if p.requires_grad)

    num_splits = len(loggers)
    split_names = ['val', 'test']
    counter, best_val_result = 0, -1
    model_path = os.path.join('denas_trained_model', args.model_save_folder, str(model_dict_len) + '.yaml')
    training_time = 0
    for cur_epoch in tqdm(range(start_epoch, cfg.optim.max_epoch), desc='training the model'):
        start_time_train = time.time()
        train_epoch(loggers[0], loaders[0], model, optimizer, scheduler)
        training_time += (time.time() - start_time_train)
        loggers[0].write_epoch(cur_epoch)
        # for i in range(1, num_splits):
        i=1  # only for validation set
        eval_epoch(loggers[i], loaders[i], model,
                    split=split_names[i - 1])
        stats = loggers[i].write_epoch(cur_epoch)
        if split_names[i - 1] == 'val':
            val_accuracy = stats[metric]

            # early stopping
            if val_accuracy > best_val_result:
                best_val_result = val_accuracy
                torch.save(model, model_path)

            #     counter = 0 
            # else:
            #     counter += 1
            #     if counter >= patience:  # perform early stop
            #         break
    

        if is_ckpt_epoch(cur_epoch):
            save_ckpt(model, optimizer, scheduler, cur_epoch)
    
    # performance on test set
    model = torch.load(model_path, map_location=torch.device(cfg.device))
    i=2  # for testing set
    eval_epoch(loggers[i], loaders[i], model,
                split=split_names[i - 1])
    stats = loggers[i].write_epoch(cur_epoch)
    if split_names[i - 1] == 'test':
        test_accuracy = stats[metric]


    for logger in loggers:
        logger.close()
    if cfg.train.ckpt_clean:
        clean_ckpt()

    return best_val_result, test_accuracy, training_time*1000, num_params

