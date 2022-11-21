import sys, codecs
from tkinter import W
#sys.stdout = codecs.getwriter("utf-8")(sys.stdout)
sys.dont_write_bytecode = True

from random import shuffle
from tqdm import tqdm
import matplotlib.pyplot as plt
import time
import datetime
import numpy as np
import random

import argparse
import subprocess
import datetime
import yaml
from shutil import copyfile
import os
import shutil
import __init__ as booger
import csv

import torch
from torchvision import models
import torch.nn as nn
import torch.nn.functional as nn_functional
import torch.optim as optim
import torch.backends.cudnn as cudnn

from collections import OrderedDict

from tensorboardX import SummaryWriter

from models import vit
from common import dataset_mod
from common import make_datalist_mod
from common import data_transform_mod

class FrameInfer:
    def __init__(self, CFG, FLAGS):
        self.cfg = CFG
        self.FLAGS = FLAGS

        self.infer_sequence = self.cfg['infer_dataset']
        
        self.csv_name = self.cfg['csv_name']

        self.weights_top_directory = self.cfg['weights_top_directory']
        self.weights_file_name = self.cfg['weights_file_name']
        self.weights_path = os.path.join(self.weights_top_directory, self.weights_file_name)

        self.infer_log_top_directory = self.cfg['infer_log_top_directory']
        self.infer_log_file_name = self.cfg['infer_log_file_name']
        self.infer_log_path = os.path.join(self.infer_log_top_directory, self.infer_log_file_name)
        
        yaml_name = self.cfg['yaml_name']
        yaml_path = self.infer_log_top_directory + yaml_name
        shutil.copy(FLAGS.config, yaml_path)

        self.index_dict_name = self.cfg['index_dict_name']
        self.index_dict_path = "../index_dict/" + self.index_dict_name

        self.index_csv_path = self.cfg['index_csv_path']

        self.img_size = int(self.cfg['hyperparameters']['img_size'])
        self.resize = int(self.cfg['hyperparameters']['resize'])
        self.patch_size = int(self.cfg['hyperparameters']['patch_size'])
        self.num_classes = int(self.cfg['hyperparameters']['num_classes'])
        self.num_frames = int(self.cfg['hyperparameters']['num_frames'])
        self.attention_type = self.cfg['hyperparameters']['attention_type']
        self.depth = int(self.cfg['hyperparameters']['depth'])
        self.num_heads = int(self.cfg['hyperparameters']['num_heads'])
        self.deg_threshold = int(self.cfg['hyperparameters']['deg_threshold'])
        self.mean_element = float(self.cfg['hyperparameters']['mean_element'])
        self.std_element = float(self.cfg['hyperparameters']['std_element'])
        self.do_white_makeup = bool(self.cfg['hyperparameters']['do_white_makeup'])
        self.do_white_makeup_from_back = bool(self.cfg['hyperparameters']['do_white_makeup_from_back'])
        self.whiteup_frame = int(self.cfg['hyperparameters']['whiteup_frame'])

        self.transform = data_transform_mod.DataTransform(self.resize, self.mean_element, self.std_element)

        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        print("Device: ", self.device)

        self.net = self.getNetwork()

        self.value_dict = []

        with open(self.index_dict_path) as fd:
            reader = csv.reader(fd)
            for row in reader:
                num = float(row[0])
                self.value_dict.append(num)


        #self.data_list = self.getDatalist()

        self.test_dataset = dataset_mod.AttitudeEstimatorDataset(
            data_list=make_datalist_mod.makeMultiDataList(self.infer_sequence, self.csv_name),
            #data_list = self.data_list,
            transform = data_transform_mod.DataTransform(
                self.resize,
                self.mean_element,
                self.std_element
            ),
            phase = "train",
            index_dict_path = self.index_csv_path,
            dim_fc_out = self.num_classes,
            timesteps = self.num_frames,
            deg_threshold = self.deg_threshold,
            resize = self.resize,
            do_white_makeup = self.do_white_makeup,
            do_white_makeup_from_back = self.do_white_makeup_from_back,
            whiteup_frame = self.whiteup_frame
        )

    def getNetwork(self):
        net = vit.TimeSformer(self.img_size, self.patch_size, self.num_classes, self.num_frames, self.depth, self.num_heads, self.attention_type, self.weights_path, 'eval')
        print("Load Network")
        print(net)
        net.to(self.device)
        net.eval()

        print("Load state_dict")
        if torch.cuda.is_available:
            state_dict = torch.load(self.weights_path, map_location=lambda storage, loc: storage)
            #print(state_dict.keys())
            new_state_dict = OrderedDict()

            for k, v in state_dict.items():
                if 'module' in k:
                    k = k.replace('module.', '')
                new_state_dict[k] = v

            state_dict = new_state_dict
            print("Load .pth file")
            #print(state_dict.keys())
        else:
            state_dict = torch.load(self.weights_path, map_location={"cuda:0": "cpu"})
            print("Load to CPU")

        #net.load_state_dict(state_dict, strict=False)
        net.load_state_dict(state_dict)
        return net

    def spin(self):
        print("Start inference")

        result_csv = []
        infer_count = 0

        diff_total_roll = 0.0
        diff_total_pitch = 0.0

        for input_image, label_roll, label_pitch in self.test_dataset:
            start_clock = time.time()
            input_image = input_image.unsqueeze(dim=0)
            #print(input_image.size())
            input_image = input_image.to(self.device)

            result = []
            roll_result_list = []
            pitch_result_list = []
            roll_hist_array = [0.0 for _ in range(self.num_classes)]
            pitch_hist_array = [0.0 for _ in range(self.num_classes)]
            roll_value_array = []
            pitch_value_array = []

            roll_inf, pitch_inf = self.prediction(input_image)

            roll = self.array_to_value_simple(roll_inf)
            pitch = self.array_to_value_simple(pitch_inf)

            correct_roll = self.array_to_value_simple_label(np.array(label_roll.to('cpu').detach().numpy().copy()))
            correct_pitch = self.array_to_value_simple_label(np.array(label_pitch.to('cpu').detach().numpy().copy()))


            roll_hist_array += roll_inf[0]
            pitch_hist_array += pitch_inf[0]

            diff_roll = np.abs(roll - correct_roll)
            diff_pitch = np.abs(pitch - correct_pitch)

            diff_total_roll += diff_roll
            diff_total_pitch += diff_pitch

            print("------------------------------------")
            print("Inference: ", infer_count)
            print("Infered Roll:  " + str(roll) +  "[deg]")
            print("GT Roll:       " + str(correct_roll) + "[deg]")
            print("Infered Pitch: " + str(pitch) + "[deg]")
            print("GT Pitch:      " + str(correct_pitch) + "[deg]")
            print("Diff Roll: " + str(diff_roll) + " [deg]")
            print("Diff Pitch: " + str(diff_pitch) + " [deg]")

            tmp_result_csv = [roll, pitch, correct_roll, correct_pitch, diff_roll, diff_pitch]
            result_csv.append(tmp_result_csv)

            print("Period [s]: ", time.time() - start_clock)
            print("------------------------------------")

            infer_count += 1
            


        print("Inference Test Has Done....")
        print("Average of Error of Roll : " + str(diff_total_roll/float(infer_count)) + " [deg]")
        print("Average of Error of Pitch: " + str(diff_total_pitch/float(infer_count)) + " [deg]")
        return result_csv

    def save_csv(self, result_csv):
        csv_file = open(self.infer_log_path, 'w')
        csv_w = csv.writer(csv_file)
        for row in result_csv:
            csv_w.writerow(row)
        csv_file.close()
        print("Save Inference Data")

    def prediction(self, img_list):
        roll_inf, pitch_inf = self.net(img_list)
        output_roll_array = roll_inf.to('cpu').detach().numpy().copy()
        output_pitch_array = pitch_inf.to('cpu').detach().numpy().copy()

        return np.array(output_roll_array), np.array(output_pitch_array)

    def array_to_value_simple(self, output_array):
        max_index = int(np.argmax(output_array))
        plus_index = max_index + 1
        minus_index = max_index - 1
        value = 0.0
        
        for tmp, label in zip(output_array[0], self.value_dict):
            value += tmp * label

        if max_index == 0:
            value = -31.0
        elif max_index == 62: #361
            value = 31.0

        return value

    def array_to_value_simple_label(self, output_array):
        max_index = int(np.argmax(output_array))
        plus_index = max_index + 1
        minus_index = max_index - 1
        value = 0.0
        
        for tmp, label in zip(output_array, self.value_dict):
            #print("val :", tmp)
            #print("label :", label)
            #print(tmp*label)
            value += tmp * label

        if max_index == 0:
            value = -31.0
        elif max_index == 62: #361
            value = 31.0

        return value

if __name__ == "__main__":
    parser = argparse.ArgumentParser("./frame_infer_vit.py")

    parser.add_argument(
        "--config", "-c",
        type=str,
        default="../pyyaml/frame_infer_vit_config.yaml",
        required=False,
        help="Infer config file path"
    )

    FLAGS, unparsed = parser.parse_known_args()

    try:
        print("Opening Infer config file %s", FLAGS.config)
        CFG = yaml.safe_load(open(FLAGS.config, 'r'))
    except Exception as e:
        print(e)
        print("Failed to open config file %s", FLAGS.config)
        quit()

    frame_infer = FrameInfer(CFG, FLAGS)
    result_csv = frame_infer.spin()
    frame_infer.save_csv(result_csv)
