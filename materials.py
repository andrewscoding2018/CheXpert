'''Material classes for 'run_chexpert.py'''

###################
## Prerequisites ##
###################
import time
import pickle
import random
import csv
import os
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from PIL import Image

import torch
import torch.nn as nn
import torch.optim as optim
import torch.backends.cudnn as cudnn
import torchvision
import torchvision.transforms as transforms
from torch.utils.data import Dataset
from torch.utils.data import DataLoader
from torch.utils.data.dataset import random_split

import sklearn.metrics as metrics
from sklearn.metrics import roc_auc_score

use_gpu = torch.cuda.is_available()



######################
## Create a Dataset ##
######################
class CheXpertDataSet(Dataset):
    def __init__(self, data_PATH, nnClassCount, transform = None):
        """
        data_PATH: path to the file containing images with corresponding labels.
        transform: optional transform to be applied on a sample.
        Upolicy: name the policy with regard to the uncertain labels.
        """
        image_names = []
        labels = []

        with open(data_PATH, 'r') as f:
            csvReader = csv.reader(f)
            next(csvReader, None) # skip the header
            for line in csvReader:
                image_name = line[0]
                label = line[5:]
                
                for i in range(nnClassCount):
                    if label[i]:
                        a = float(label[i])
                        if a == 1:
                            label[i] = 1
                        elif a == -1:
                            if i == 10 or i == 13:
                                label[i] = 1
                            elif i == 7 or i == 11 or i == 15:
                                label[i] = 0
                            else:
                                label[i] = 0
                        else:
                            label[i] = 0
                    else:
                        label[i] = 0
                        
                image_names.append('./' + image_name)
                labels.append(label)

        self.image_names = image_names
        self.labels = labels
        self.transform = transform

    def __getitem__(self, index):
        '''Take the index of item and returns the image and its labels'''
        image_name = self.image_names[index]
        image = Image.open(image_name).convert('RGB')
        label = self.labels[index]
        if self.transform is not None:
            image = self.transform(image)
        return image, torch.FloatTensor(label)

    def __len__(self):
        return len(self.image_names)


############################
## Create CheXpertTrainer ##
############################
class CheXpertTrainer():
    def train(model, dataLoaderTrain, dataLoaderVal, nnClassCount, trMaxEpoch, PATH, f_or_l, checkpoint):
        optimizer = optim.Adam(model.parameters(), lr = 0.0001, # setting optimizer & scheduler
                               betas = (0.9, 0.999), eps = 1e-08, weight_decay = 0) 
        loss = torch.nn.BCELoss() # setting loss function
        
        if checkpoint != None and use_gpu: # loading checkpoint
            modelCheckpoint = torch.load(checkpoint)
            model.load_state_dict(modelCheckpoint['state_dict'])
            optimizer.load_state_dict(modelCheckpoint['optimizer'])
            
        # Train the network
        lossMIN = 100000
        train_start = []
        train_end = []
        print('<<< Training & Evaluating ({}) >>>'.format(f_or_l))
        for epochID in range(0, trMaxEpoch):
            train_start.append(time.time()) # training starts
            losst = CheXpertTrainer.epochTrain(model, dataLoaderTrain, optimizer, trMaxEpoch, nnClassCount, loss)
            train_end.append(time.time())   # training ends
            lossv = CheXpertTrainer.epochVal(model, dataLoaderVal, optimizer, trMaxEpoch, nnClassCount, loss)
            print("Training loss: {:.3f},".format(losst), "Valid loss: {:.3f}".format(lossv))
            
            if lossv < lossMIN:
                lossMIN = lossv
                model_num = epochID + 1
                torch.save({'epoch': epochID + 1, 'state_dict': model.state_dict(), 
                            'best_loss': lossMIN, 'optimizer' : optimizer.state_dict()}, 
                            '{0}m-epoch_{1}_{2}.pth.tar'.format(PATH, epochID + 1, f_or_l))
                print('Epoch ' + str(epochID + 1) + ' [save] loss = ' + str(lossv))
            else:
                print('Epoch ' + str(epochID + 1) + ' [----] loss = ' + str(lossv))
        
        train_time = np.array(train_end) - np.array(train_start)
        return model_num, train_time
       
        
    def epochTrain(model, dataLoaderTrain, optimizer, epochMax, classCount, loss):
        model.train()
        losstrain = 0

        for batchID, (varInput, target) in enumerate(dataLoaderTrain):
            optimizer.zero_grad()
            
            varTarget = target.cuda(non_blocking = True)
            varOutput = model(varInput)
            lossvalue = loss(varOutput, varTarget)
                       
            lossvalue.backward()
            optimizer.step()
            
            losstrain += lossvalue.item()
            if batchID % 1000 == 999:
                print('[Batch: %5d] loss: %.3f'%(batchID + 1, losstrain / 2000))
            
        return losstrain / len(dataLoaderTrain)
    
    
    def epochVal(model, dataLoaderVal, optimizer, epochMax, classCount, loss):
        model.eval()
        lossVal = 0

        with torch.no_grad():
            for i, (varInput, target) in enumerate(dataLoaderVal):
                
                target = target.cuda(non_blocking = True)
                varOutput = model(varInput)
                
                lossVal += loss(varOutput, target)
                
        return lossVal / len(dataLoaderVal)

    
    def computeAUROC(dataGT, dataPRED, classCount):
        # Computes area under ROC curve 
        # dataGT: ground truth data
        # dataPRED: predicted data
        outAUROC = []
        datanpGT = dataGT.cpu().numpy()
        datanpPRED = dataPRED.cpu().numpy()
        
        for i in range(classCount):
            try:
                outAUROC.append(roc_auc_score(datanpGT[:, i], datanpPRED[:, i]))
            except ValueError:
                pass
        return outAUROC
    
    
    def test(model, dataLoaderTest, nnClassCount, checkpoint, class_names, f_or_l):
        cudnn.benchmark = True
        
        if checkpoint != None and use_gpu:
            modelCheckpoint = torch.load(checkpoint)
            model.load_state_dict(modelCheckpoint['state_dict'])

        if use_gpu:
            outGT = torch.FloatTensor().cuda()
            outPRED = torch.FloatTensor().cuda()
        else:
            outGT = torch.FloatTensor()
            outPRED = torch.FloatTensor()
       
        model.eval()
        
        outPROB = []
        with torch.no_grad():
            for i, (input, target) in enumerate(dataLoaderTest):

                target = target.cuda()
                outGT = torch.cat((outGT, target), 0).cuda()
                outProb = model(input) # probability
                outProb = outProb.tolist()
                outPROB.append(outProb)

                bs, c, h, w = input.size()
                varInput = input.view(-1, c, h, w)
                
                out = model(varInput)
                outPRED = torch.cat((outPRED, out), 0)
        aurocIndividual = CheXpertTrainer.computeAUROC(outGT, outPRED, nnClassCount)
        aurocMean = np.array(aurocIndividual).mean()
        print('<<< Model Test Results ({}) >>>'.format(f_or_l))
        print('AUROC mean ', aurocMean)
        
        for i in range (0, len(aurocIndividual)):
            print(class_names[i], ' ', aurocIndividual[i])
        
        return outGT, outPRED, outPROB, aurocMean, aurocIndividual



##################
## Define Model ##
##################
class DenseNet121(nn.Module):
    '''Model modified.
    The architecture of this model is the same as standard DenseNet121
    except the classifier layer which has an additional sigmoid function.
    '''
    def __init__(self, out_size, nnIsTrained):
        super(DenseNet121, self).__init__()
        self.densenet121 = torchvision.models.densenet121(pretrained = nnIsTrained)
        num_ftrs = self.densenet121.classifier.in_features
        out_ftrs = self.densenet121.classifier.out_features
        self.densenet121.classifier = nn.Sequential(
            nn.Linear(num_ftrs, out_ftrs),
            nn.Linear(out_ftrs, out_size), # add FC layer
            nn.Sigmoid()
        )

    def forward(self, x):
        x = self.densenet121(x)
        return x



###############################
## Define Ensembles Function ##
###############################
def EnsemAgg(EnsemResult, dataLoader, nnClassCount, class_names):
    outGT = torch.FloatTensor().cuda()
    outPRED = torch.FloatTensor().cuda()
    with torch.no_grad():
        for i, (input, target) in enumerate(dataLoader):
            target = target.cuda()
            outGT = torch.cat((outGT, target), 0).cuda()
            
            bs, c, h, w = input.size()
            varInput = input.view(-1, c, h, w)

            # out = model(varInput)
            out = torch.tensor([EnsemResult[i]]).cuda()
            outPRED = torch.cat((outPRED, out), 0)
    aurocIndividual = CheXpertTrainer.computeAUROC(outGT, outPRED, nnClassCount)
    aurocMean = np.array(aurocIndividual).mean()
    print('<<< Ensembles Test Results >>>')
    print('AUROC mean ', aurocMean)

    for i in range (0, len(aurocIndividual)):
        print(class_names[i], ' ', aurocIndividual[i])

    return outGT, outPRED, aurocMean, aurocIndividual