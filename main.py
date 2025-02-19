
import math
import os
import numpy as np
import pandas as pd
import matplotlib as mpl
mpl.use('Agg')
from utils import *
from data_reading import *
from models import  DualEncoderModel
import torch
import torch.nn as nn
import torch.nn.functional as F
from train_test_split import kf_split
from sklearn.metrics import roc_curve, auc, average_precision_score
import matplotlib.pyplot as plt
import argparse
from config_init import get_config
torch.cuda.manual_seed(1223)

if __name__=="__main__":
    config = get_config()
    root_path = config.root_path_topofallfeature
    dataset = config.dataset_topofallfeature
    drug_sim_file = config.drug_sim_file_topofallfeature
    target_sim_file = config.target_sim_file_topofallfeature
    dti_mat = config.dti_mat_topofallfeature
    device = config.device_topofallfeature
    n_splits = config.n_splits_topofallfeature
    hgcn_dim = config.hgcn_dim_topofallfeature

    hidden_dim = config.hidden_dim_topofallfeature
    K = config.K_topofallfeature
    alpha = config.alpha_topofallfeature
    w = config.w_topofallfeature
    pos_weight = config.pos_weight_topofallfeature

    dropout = config.dropout_topofallfeature
    epoch_num = config.epoch_num_topofallfeature
    lr = config.lr_topofallfeature
    topk = config.topk_topofallfeature
    epoch_interv = config.epoch_interv_topofallfeature

    # data reading
    data_folder = os.path.join(root_path, dataset)
    drug_sim_path = os.path.join(data_folder, drug_sim_file)
    target_sim_path = os.path.join(data_folder, target_sim_file)
    DTI_path = os.path.join(data_folder, dti_mat)

    SR, SD, A_orig, A_orig_arr, known_sample = read_data(data_folder, drug_sim_path, target_sim_path, DTI_path)

    SR = SR[300:]   # This parameter remains unchanged.
    SR = SR.flatten()
    SR = string_float(SR)

    '''this parameter needs to be adjusted according to the dataset. 
    For example, when running on the KIBA dataset, which contains 1,720 drugs, 
    this line should be set as SR = SR.reshape(1720, 1720).'''
    SR = SR.reshape(1821, 1821)
    SD = SD[200:]   # This parameter remains unchanged.
    SD = SD.flatten()
    SD = string_float(SD)

    '''this parameter needs to be adjusted according to the dataset. 
      For example, when running on the KIBA dataset, which contains 220 drugs, 
      this line should be set as SR = SR.reshape(220, 220).'''
    SD = SD.reshape(1447, 1447)
    SR = Global_Normalize(SR)  # drug affinity matrix
    SD = Global_Normalize(SD)  # target affinity matrix
    drug_num = A_orig.shape[0]
    target_num = A_orig.shape[1]
    A_orig_list = A_orig.flatten()  # known drug-target affinity matrix
    drug_dissimmat = get_drug_dissimmat(SR, topk=topk).astype(int)
    negtive_index_arr = np.where(A_orig_arr == 0)[0]
    negative_index = torch.LongTensor(negtive_index_arr)

    # kfold CV
    train_all, test_all = kf_split(known_sample, n_splits)
    overall_auroc = 0
    overall_aupr = 0
    overall_f1 = 0
    overall_acc = 0
    overall_recall = 0
    overall_specificity = 0
    overall_precision = 0

    for fold_int in range(n_splits):
        print('fold_int:', fold_int)
        A_train_id = train_all[fold_int]
        A_test_id = test_all[fold_int]
        A_train = known_sample[A_train_id]
        A_test = known_sample[A_test_id]
        A_train_tensor = torch.LongTensor(A_train)
        A_test_tensor = torch.LongTensor(A_test)
        A_train_list = np.zeros_like(A_orig_arr)
        A_train_list[A_train] = 1
        A_test_list = np.zeros_like(A_orig_arr)
        A_test_list[A_test] = 1
        A_train_mask = A_train_list.reshape((A_orig.shape[0], A_orig.shape[1]))
        A_test_mask = A_test_list.reshape((A_orig.shape[0], A_orig.shape[1]))
        A_unknown_mask = 1 - A_orig
        A_train_mat = A_train_mask

        G = Construct_G(A_train_mat, SR, SD).to(device)  # G is the normalized adjacent matrix
        H = Construct_H(A_train_mat, SR, SD).to(device)  # H is the feature embedding matrix for update

        # sample the negative samples
        train_neg_mask_candidate = get_negative_samples(A_train_mask, drug_dissimmat)
        train_neg_mask = np.multiply(train_neg_mask_candidate, A_unknown_mask)
        train_negative_index = np.where(train_neg_mask.flatten() == 1)[0]
        training_negative_index = torch.tensor(train_negative_index)

        # initizalize the model
        train_W = torch.randn(hgcn_dim, hgcn_dim).to(device)
        train_W = nn.init.xavier_normal_(train_W)
        model = DualEncoderModel(in_dim=H.size(0), hgcn_dim=hgcn_dim, hidden_dim=hidden_dim, K=K, alpha=alpha, Init='PPR', train_W=train_W, dropout=dropout).to(device)
        model_optimizer = torch.optim.Adam(list(model.parameters()), lr=lr)

        # train procedure
        model.train()
        for epoch in range(epoch_num):
            # prediction results
            A_hat = model(H, G, drug_num, target_num, w=w)
            A_hat_list = A_hat.view(1, -1)
            train_sample = A_hat_list[0][A_train_tensor]
            train_score = torch.sigmoid(train_sample)
            nega_sample = A_hat_list[0][training_negative_index]
            nega_score = torch.sigmoid(nega_sample)
            # calculate the loss
            if topk == 1:
                loss = loss_function(train_score, nega_score, drug_num, target_num)
                los_ = loss.detach().item()
                model_optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                model_optimizer.step()
                if epoch % epoch_interv == 0:
                    print('loss:', los_)
            else:
                loss = weighted_loss_function(train_score, nega_score, drug_num, target_num, pos_weight)
                los_ = loss.detach().item()
                model_optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                model_optimizer.step()
                if epoch % epoch_interv == 0:
                    print('loss:', los_)

        # test procedure
        model.eval()
        test_neg_mask_candidate = get_negative_samples(A_test_mask, drug_dissimmat)
        test_neg_mask = np.multiply(test_neg_mask_candidate, A_unknown_mask)
        test_negative_index = np.where(test_neg_mask.flatten() == 1)[0]
        test_negative_index = torch.tensor(test_negative_index)

        positive_samples = A_hat_list[0][A_test_tensor].detach().cpu().numpy()
        negative_samples = A_hat_list[0][test_negative_index].detach().cpu().numpy()
        positive_labels = np.ones_like(positive_samples)
        negative_labels = np.zeros_like(negative_samples)
        labels = np.hstack((positive_labels, negative_labels))
        scores = np.hstack((positive_samples, negative_samples))
        TP, FP, FN, TN, fpr, tpr, auroc, aupr, f1_score, accuracy, recall, specificity, precision = get_metric(labels,scores)

        print('TP:', TP)
        print('FP:', FP)
        print('FN:', FN)
        print('TN:', FN)
        print('fpr:', fpr)
        print('tpr:', tpr)
        print('auroc:', auroc)
        print('aupr:', aupr)
        print('f1_score:', f1_score)
        print('acc:', accuracy)
        print('recall:', recall)
        print('specificity:', specificity)
        print('precision:', precision)
        overall_auroc += auroc
        overall_aupr += aupr
        overall_f1 += f1_score
        overall_acc += accuracy
        overall_recall += recall
        overall_specificity += specificity
        overall_precision += precision
    auroc_ = overall_auroc / n_splits
    aupr_ = overall_aupr / n_splits
    f1_ = overall_f1 / n_splits
    acc_ = overall_acc / n_splits
    recall_ = overall_recall / n_splits
    specificity_ = overall_specificity / n_splits
    precision_ = overall_precision / n_splits
    print('mean_auroc:', auroc_)
    print('mean_aupr:', aupr_)
    print('mean_f1:', f1_)
    print('mean_acc:', acc_)
    print('mean_recall:', recall_)
    print('mean_specificity:', specificity_)
    print('mean_precision:', precision_)
