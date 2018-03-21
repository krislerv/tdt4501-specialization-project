import datetime
import os
import time
import numpy as np
from models_attn_h import InterRNN, InterRNN2, IntraRNN, Embed
from datahandler_attn_h import IIRNNDataHandler
from test_util import Tester

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import optim
from torch.autograd import Variable

from tensorboard import Logger as TensorBoard

import gpustat

import gc

# datasets
reddit = "reddit-2-month"
lastfm = "lastfm-3-months"
dataset = lastfm

# which type of session representation to use. False: Average pooling, True: Last hidden state
use_last_hidden_state = False

# Inter-session attention mechanisms
use_hidden_state_attn = False
use_delta_t_attn = False
use_week_time_attn = False

# Intra-session attention mechanisms
use_intra_attn = False
user_intra_delta_t_attn = False # not used if use_intra_attn is False
use_per_user_intra_attn = False # not used if use_intra_attn is False

# logging of attention weights
log_inter_attn = False
log_intra_attn = True

# saving/loading of model parameters
save_model_parameters = True
resume_model = False
resume_model_name = "2018-03-07-18-04-35-testing-attn-rnn-lastfm-low-low-True-False-True"    # unused if resume_model is False

# GPU settings
use_cuda = True
GPU_NO = 0

# dataset path
HOME = os.path.expanduser('~')
DATASET_PATH = HOME + '/datasets/' + dataset + '/4_train_test_split.pickle'

# logging of testing results
DATE_NOW = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d')
TIME_NOW = datetime.datetime.fromtimestamp(time.time()).strftime('%H-%M-%S')
if resume_model:
    RUN_NAME = resume_model_name
else:   # pre 2018-03-06: Three boolean values were inter attn mechanisms, post: they are intra attn mechanisms
    RUN_NAME = str(DATE_NOW) + '-' + str(TIME_NOW) + '-testing-attn-rnn-' + dataset + '-' + str(use_intra_attn) + '-' + str(user_intra_delta_t_attn) + '-' + str(use_per_user_intra_attn)
LOG_FILE = './testlog/' + RUN_NAME + '.txt'
tensorboard = TensorBoard('./logs')

# set seed
seed = 0
torch.manual_seed(seed)

# RNN configuration
if dataset == reddit:
    INTRA_INTERNAL_SIZE = 50
    INTER_INTERNAL_SIZE = INTRA_INTERNAL_SIZE
    LEARNING_RATE = 0.001
    DROPOUT_RATE = 0.0
    MAX_EPOCHS = 31
elif dataset == lastfm:
    INTRA_INTERNAL_SIZE = 100
    INTER_INTERNAL_SIZE = INTRA_INTERNAL_SIZE
    LEARNING_RATE = 0.001
    DROPOUT_RATE = 0.2
    MAX_EPOCHS = 200
N_LAYERS     = 1
EMBEDDING_SIZE = INTRA_INTERNAL_SIZE
TOP_K = 20
N_ITEMS      = -1
BATCH_SIZE    = 60
MAX_SESSION_REPRESENTATIONS = 15

# Load training data
datahandler = IIRNNDataHandler(DATASET_PATH, BATCH_SIZE, LOG_FILE, MAX_SESSION_REPRESENTATIONS, INTER_INTERNAL_SIZE)
N_ITEMS = datahandler.get_num_items()
N_SESSIONS = datahandler.get_num_training_sessions()

message = "------------------------------------------------------------------------\n"
if use_last_hidden_state:
    message += dataset + " with last hidden state\n"
else:
    message += dataset + " with average of embeddings\n"
message += "DATASET: " + dataset + " MODEL: attn-RNN"
message += "\nuse_hidden_state_attn=" + str(use_hidden_state_attn) + " use_delta_t_attn=" + str(use_delta_t_attn) + " use_week_time_attn=" + str(use_week_time_attn)
message += "\nuse_intra_attn=" + str(use_intra_attn) + " user_intra_delta_t_attn=" + str(user_intra_delta_t_attn) + " use_per_user_intra_attn=" + str(use_per_user_intra_attn)
message += "\nCONFIG: N_ITEMS=" + str(N_ITEMS) + " BATCH_SIZE=" + str(BATCH_SIZE)
message += "\nINTRA_INTERNAL_SIZE=" + str(INTRA_INTERNAL_SIZE) + " INTER_INTERNAL_SIZE=" + str(INTER_INTERNAL_SIZE)
message += "\nN_LAYERS=" + str(N_LAYERS) + " EMBEDDING_SIZE=" + str(EMBEDDING_SIZE)
message += "\nN_SESSIONS=" + str(N_SESSIONS) + " SEED="+str(seed)
message += "\nMAX_SESSION_REPRESENTATIONS=" + str(MAX_SESSION_REPRESENTATIONS)
message += "\nDROPOUT_RATE=" + str(DROPOUT_RATE) + " LEARNING_RATE=" + str(LEARNING_RATE)
print(message)

def show_memusage(device=0):
    gpu_stats = gpustat.GPUStatCollection.new_query()
    item = gpu_stats.jsonify()["gpus"][device]
    print("{}/{}".format(item["memory.used"], item["memory.total"]))

embed = Embed(N_ITEMS, EMBEDDING_SIZE)
if resume_model:
    embed.load_state_dict(torch.load(HOME + "/savestates/" + RUN_NAME + "-embed_model.pth"))
if use_cuda:
    embed = embed.cuda(GPU_NO)
embed_optimizer = optim.Adam(embed.parameters(), lr=LEARNING_RATE)
if resume_model:
    embed_optimizer.load_state_dict(torch.load(HOME + "/savestates/" + RUN_NAME + "-embed_optimizer.pth"))

# initialize inter RNN
inter_rnn = InterRNN(EMBEDDING_SIZE, INTER_INTERNAL_SIZE, N_LAYERS, DROPOUT_RATE, MAX_SESSION_REPRESENTATIONS, use_hidden_state_attn=use_hidden_state_attn, use_delta_t_attn=use_delta_t_attn, use_week_time_attn=use_week_time_attn, gpu_no=GPU_NO)
if resume_model:
    inter_rnn.load_state_dict(torch.load(HOME + "/savestates/" + RUN_NAME + "-inter_model.pth"))
if use_cuda:
    inter_rnn = inter_rnn.cuda(GPU_NO)
inter_optimizer = optim.Adam(inter_rnn.parameters(), lr=LEARNING_RATE)
if resume_model:
    inter_optimizer.load_state_dict(torch.load(HOME + "/savestates/" + RUN_NAME + "-inter_optimizer.pth"))


# initialize inter RNN 2
inter_rnn2 = InterRNN2(EMBEDDING_SIZE, INTER_INTERNAL_SIZE, N_LAYERS, DROPOUT_RATE, MAX_SESSION_REPRESENTATIONS, use_hidden_state_attn=use_hidden_state_attn, use_delta_t_attn=use_delta_t_attn, use_week_time_attn=use_week_time_attn, gpu_no=GPU_NO)
if use_cuda:
    inter_rnn2 = inter_rnn2.cuda(GPU_NO)
inter_optimizer2 = optim.Adam(inter_rnn2.parameters(), lr=LEARNING_RATE)

# initialize intra RNN
intra_rnn = IntraRNN(N_ITEMS, INTRA_INTERNAL_SIZE, EMBEDDING_SIZE, N_LAYERS, DROPOUT_RATE, MAX_SESSION_REPRESENTATIONS, use_attn=use_intra_attn, use_delta_t_attn=user_intra_delta_t_attn, use_per_user_intra_attn=use_per_user_intra_attn, gpu_no=GPU_NO)
if resume_model:
    intra_rnn.load_state_dict(torch.load(HOME + "/savestates/" + RUN_NAME + "-intra_model.pth"))
if use_cuda:
    intra_rnn = intra_rnn.cuda(GPU_NO)
intra_optimizer = optim.Adam(intra_rnn.parameters(), lr=LEARNING_RATE)
if resume_model:
    intra_optimizer.load_state_dict(torch.load(HOME + "/savestates/" + RUN_NAME + "-intra_optimizer.pth"))

def train(input, target, session_lengths, session_reps, inter_session_seq_length, use_last_hidden_state, input_timestamps, input_timestamp_bucket_ids, sess_rep_timestamps_batch, sess_rep_timestamp_bucket_ids_batch, user_list, previous_session_batch, previous_session_lengths, prevoius_session_counts):
    inter_optimizer.zero_grad()
    inter_optimizer2.zero_grad()
    intra_optimizer.zero_grad()
    embed_optimizer.zero_grad()

    input = Variable(torch.LongTensor(input))
    target = Variable(torch.LongTensor(target))
    session_lengths = Variable(torch.LongTensor(session_lengths).view(-1, 1)) # by reshaping the length to this, it can be broadcasted and used for division.
    session_reps = Variable(torch.FloatTensor(session_reps))
    inter_session_seq_length = Variable(torch.LongTensor(inter_session_seq_length))
    input_timestamps = Variable(torch.FloatTensor(input_timestamps))
    sess_rep_timestamps_batch = Variable(torch.FloatTensor(sess_rep_timestamps_batch))
    sess_rep_timestamp_bucket_ids_batch = Variable(torch.LongTensor(sess_rep_timestamp_bucket_ids_batch))
    user_list = Variable(torch.LongTensor((user_list).tolist()))

    previous_session_batch = Variable(torch.LongTensor(previous_session_batch))
    previous_session_lengths = Variable(torch.LongTensor(previous_session_lengths))
    prevoius_session_counts = Variable(torch.LongTensor(prevoius_session_counts))

    if use_cuda:
        input = input.cuda(GPU_NO)
        #input_embedding = input_embedding.cuda(GPU_NO)
        target = target.cuda(GPU_NO)
        session_lengths = session_lengths.cuda(GPU_NO)
        session_reps = session_reps.cuda(GPU_NO)
        inter_session_seq_length = inter_session_seq_length.cuda(GPU_NO)
        input_timestamps = input_timestamps.cuda(GPU_NO)
        sess_rep_timestamps_batch = sess_rep_timestamps_batch.cuda(GPU_NO)
        sess_rep_timestamp_bucket_ids_batch = sess_rep_timestamp_bucket_ids_batch.cuda(GPU_NO)
        user_list = user_list.cuda(GPU_NO)

        previous_session_batch = previous_session_batch.cuda(GPU_NO)
        previous_session_lengths = previous_session_lengths.cuda(GPU_NO)
        prevoius_session_counts = prevoius_session_counts.cuda(GPU_NO)

    input_embedding = embed(input)

    previous_session_batch = previous_session_batch.transpose(0, 1)     # max_sess_rep x batch_size x max_sess_length
    previous_session_lengths = previous_session_lengths.transpose(0, 1) # max_sess_rep x batch_size

    inter_hidden = inter_rnn.init_hidden(previous_session_batch.size(1), use_cuda)

    all_session_representations = Variable(torch.zeros(MAX_SESSION_REPRESENTATIONS, previous_session_batch.size(1), INTER_INTERNAL_SIZE)).cuda(GPU_NO)

    for i in range(previous_session_batch.size(0)):
        current_session_batch = previous_session_batch[i]   # batch_size x max_sess_length
        current_session_lengths = previous_session_lengths[i]
        current_session_batch = embed(current_session_batch)
        inter_output, inter_hidden, inter_attn_weights, session_representations = inter_rnn(inter_hidden, current_session_batch, current_session_lengths)
        all_session_representations[i] = session_representations

    all_session_representations = all_session_representations.transpose(0, 1)
    inter2_hidden = inter_rnn2.init_hidden(previous_session_batch.size(1), use_cuda)
    inter2_output, inter2_hidden, inter2_attn_weights, user_representations = inter_rnn2(inter2_hidden, all_session_representations, prevoius_session_counts)

    loss = 0

    # call forward on intra gru layer with hidden state from inter
    intra_hidden = user_representations.transpose(0, 1)
    for i in range(input.size(1)):
        b = Variable(torch.LongTensor([i]).expand(input.size(0), 1))
        ee = Variable(torch.LongTensor([i]).expand(input.size(0), 1, EMBEDDING_SIZE))
        if use_cuda:
            b = b.cuda(GPU_NO)
            ee = ee.cuda(GPU_NO)
        c = torch.gather(input, 1, b)
        e = torch.gather(input_embedding, 1, ee)
        t = torch.gather(target, 1, b)
        out, intra_hidden, embedded_input, gru, attn_weights = intra_rnn(c, e, intra_hidden, inter2_output, user_list)
        loss += masked_cross_entropy_loss(out.squeeze(), t.squeeze()).mean(0)
        if i == 0:
            output = out
            gru_output = gru
            cat_embedded_input = embedded_input
            if use_intra_attn:
                intra_attn_weights = attn_weights.unsqueeze(1)
        else:
            output = torch.cat((output, out), 1)
            gru_output = torch.cat((gru_output, gru), 1)
            cat_embedded_input = torch.cat((cat_embedded_input, embedded_input), 1)
            if use_intra_attn:
                intra_attn_weights = torch.cat((intra_attn_weights, attn_weights.unsqueeze(1)), 1)

    if not use_intra_attn:
        intra_attn_weights = []
    

    # get last hidden states for session representations
    last_index_of_sessions = session_lengths - 1
    hidden_indices = last_index_of_sessions.view(-1, 1, 1).expand(gru_output.size(0), 1, gru_output.size(2))
    hidden_out = torch.gather(gru_output, 1, hidden_indices)
    hidden_out = hidden_out.squeeze()
    hidden_out = hidden_out.unsqueeze(0)

    # get average pooling of input for session representations
    sum_x = cat_embedded_input.sum(1)
    mean_x = sum_x.div(session_lengths.float())

    loss.backward()

    embed_optimizer.step()
    inter_optimizer.step()
    inter_optimizer2.step()
    intra_optimizer.step()

    top_k_values, top_k_predictions = torch.topk(output, TOP_K)

    # return loss and new session representation
    if use_last_hidden_state:
        return loss.data[0], hidden_out.data[0], inter_attn_weights, intra_attn_weights, top_k_predictions
    return loss.data[0], mean_x.data, inter_attn_weights, intra_attn_weights, top_k_predictions

def predict(input, session_lengths, session_reps, inter_session_seq_length, input_timestamps, input_timestamp_bucket_ids, sess_rep_timestamps_batch, sess_rep_timestamp_bucket_ids_batch, user_list, previous_session_batch, previous_session_lengths, prevoius_session_counts):
    input = Variable(torch.LongTensor(input))
    session_lengths = Variable(torch.LongTensor(session_lengths).view(-1, 1)) # by reshaping the length to this, it can be broadcasted and used for division.
    session_reps = Variable(torch.FloatTensor(session_reps))
    inter_session_seq_length = Variable(torch.LongTensor(inter_session_seq_length))
    input_timestamps = Variable(torch.FloatTensor(input_timestamps))
    sess_rep_timestamps_batch = Variable(torch.FloatTensor(sess_rep_timestamps_batch))
    sess_rep_timestamp_bucket_ids_batch = Variable(torch.LongTensor(sess_rep_timestamp_bucket_ids_batch))
    user_list = Variable(torch.LongTensor((user_list).tolist()))

    previous_session_batch = Variable(torch.LongTensor(previous_session_batch))
    previous_session_lengths = Variable(torch.LongTensor(previous_session_lengths))
    prevoius_session_counts = Variable(torch.LongTensor(prevoius_session_counts))

    if use_cuda:
        input = input.cuda(GPU_NO)
        #input_embedding = input_embedding.cuda(GPU_NO)
        session_lengths = session_lengths.cuda(GPU_NO)
        session_reps = session_reps.cuda(GPU_NO)
        inter_session_seq_length = inter_session_seq_length.cuda(GPU_NO)
        input_timestamps = input_timestamps.cuda(GPU_NO)
        sess_rep_timestamps_batch = sess_rep_timestamps_batch.cuda(GPU_NO)
        sess_rep_timestamp_bucket_ids_batch = sess_rep_timestamp_bucket_ids_batch.cuda(GPU_NO)
        user_list = user_list.cuda(GPU_NO)

        previous_session_batch = previous_session_batch.cuda(GPU_NO)
        previous_session_lengths = previous_session_lengths.cuda(GPU_NO)
        prevoius_session_counts = prevoius_session_counts.cuda(GPU_NO)

    input_embedding = embed(input)

    previous_session_batch = previous_session_batch.transpose(0, 1)     # max_sess_rep x batch_size x max_sess_length
    previous_session_lengths = previous_session_lengths.transpose(0, 1) # max_sess_rep x batch_size

    inter_hidden = inter_rnn.init_hidden(previous_session_batch.size(1), use_cuda)

    all_session_representations = Variable(torch.zeros(MAX_SESSION_REPRESENTATIONS, previous_session_batch.size(1), INTER_INTERNAL_SIZE)).cuda(GPU_NO)

    for i in range(previous_session_batch.size(0)):
        current_session_batch = previous_session_batch[i]   # batch_size x max_sess_length
        current_session_lengths = previous_session_lengths[i]
        current_session_batch = embed(current_session_batch)
        inter_output, inter_hidden, inter_attn_weights, session_representations = inter_rnn(inter_hidden, current_session_batch, current_session_lengths)
        all_session_representations[i] = session_representations

    all_session_representations = all_session_representations.transpose(0, 1)
    inter2_hidden = inter_rnn2.init_hidden(previous_session_batch.size(1), use_cuda)
    inter2_output, inter2_hidden, inter2_attn_weights, user_representations = inter_rnn2(inter2_hidden, all_session_representations, prevoius_session_counts)

    intra_hidden = user_representations.transpose(0, 1)
    for i in range(input.size(1)):
        b = Variable(torch.LongTensor([i]).expand(input.size(0), 1))
        ee = Variable(torch.LongTensor([i]).expand(input.size(0), 1, EMBEDDING_SIZE))
        if use_cuda:
            b = b.cuda(GPU_NO)
            ee = ee.cuda(GPU_NO)
        c = torch.gather(input, 1, b)
        e = torch.gather(input_embedding, 1, ee)
        out, intra_hidden, embedded_input, gru, attn_weights = intra_rnn(c, e, intra_hidden, inter_output, user_list)
        if i == 0:
            output = out
            gru_output = gru
            cat_embedded_input = embedded_input
            if use_intra_attn:
                intra_attn_weights = attn_weights.unsqueeze(1)
        else:
            output = torch.cat((output, out), 1)
            gru_output = torch.cat((gru_output, gru), 1)
            cat_embedded_input = torch.cat((cat_embedded_input, embedded_input), 1)
            if use_intra_attn:
                intra_attn_weights = torch.cat((intra_attn_weights, attn_weights.unsqueeze(1)), 1)
    if not use_intra_attn:
        intra_attn_weights = []

    # get last hidden states for session representations
    last_index_of_sessions = session_lengths - 1
    hidden_indices = last_index_of_sessions.view(-1, 1, 1).expand(gru_output.size(0), 1, gru_output.size(2))
    hidden_out = torch.gather(gru_output, 1, hidden_indices)
    hidden_out = hidden_out.squeeze()
    hidden_out = hidden_out.unsqueeze(0)

    # get average pooling of input for session representations
    sum_x = cat_embedded_input.sum(1)
    mean_x = sum_x.div(session_lengths.float())

    top_k_values, top_k_predictions = torch.topk(output, TOP_K)

    if use_last_hidden_state:
        return top_k_predictions, hidden_out.data[0], inter_attn_weights, intra_attn_weights
    return top_k_predictions, mean_x.data, inter_attn_weights, intra_attn_weights

#CUSTOM CROSS ENTROPY LOSS(Replace as soon as pytorch has implemented an option for non-summed losses)
#https://github.com/pytorch/pytorch/issues/264
def masked_cross_entropy_loss(y_hat, y):
    logp = -F.log_softmax(y_hat)
    logpy = torch.gather(logp, 1, y.view(-1, 1))
    mask = Variable(y.data.float().sign().view(-1, 1))
    logpy = logpy * mask
    return logpy.view(-1)

def to_np(x):
    return x.data.cpu().numpy()

##
##  TRAINING
##
print("Starting training.")

epoch = 1
log_count = 0

print()

num_training_batches = datahandler.get_num_training_batches()
num_test_batches = datahandler.get_num_test_batches()
while epoch <= MAX_EPOCHS:
    print("Starting epoch #" + str(epoch))
    epoch_loss = 0

    datahandler.reset_user_batch_data()
    datahandler.reset_user_session_representations()
    _batch_number = 0
    xinput, targetvalues, sl, input_timestamps, input_timestamp_bucket_ids, session_reps, inter_session_seq_length, sess_rep_timestamps_batch, sess_rep_timestamp_bucket_ids_batch, user_list, previous_session_batch, previous_session_lengths, prevoius_session_counts = datahandler.get_next_train_batch()
    intra_rnn.train()
    inter_rnn.train()
    while len(xinput) > int(BATCH_SIZE / 2):
        _batch_number += 1
        batch_start_time = time.time()


        batch_loss, sess_rep, inter_attn_weights, intra_attn_weights, top_k_predictions = train(xinput, targetvalues, sl, session_reps, inter_session_seq_length, use_last_hidden_state, input_timestamps, input_timestamp_bucket_ids, sess_rep_timestamps_batch, sess_rep_timestamp_bucket_ids_batch, user_list, previous_session_batch, previous_session_lengths, prevoius_session_counts)


        # log inter attention weights
        if log_inter_attn and (use_hidden_state_attn + use_delta_t_attn + use_week_time_attn > 0) and _batch_number % 100 == 0 and inter_session_seq_length[0] == 15:
            datahandler.log_attention_weights_inter(use_hidden_state_attn, use_delta_t_attn, use_week_time_attn, user_list[0], inter_attn_weights, input_timestamps, dataset)

        
        datahandler.store_user_session_representations(sess_rep, user_list, input_timestamps, input_timestamp_bucket_ids)

        epoch_loss += batch_loss
        if _batch_number % 100 == 0:
            batch_runtime = time.time() - batch_start_time
            print("Batch number:", str(_batch_number), "/", str(num_training_batches), "\t Batch time:", "%.4f" % batch_runtime, "minutes", end='')
            print("\t Batch loss:", "%.3f" % batch_loss, end='')
            eta = (batch_runtime * (num_training_batches - _batch_number)) / 60
            eta = "%.2f" % eta
            print("\t ETA:", eta, "minutes.")


            #============ TensorBoard logging ============#
            tensorboard.scalar_summary('batch_loss', batch_loss, log_count)
            log_count += 1
        
        xinput, targetvalues, sl, input_timestamps, input_timestamp_bucket_ids, session_reps, inter_session_seq_length, sess_rep_timestamps_batch, sess_rep_timestamp_bucket_ids_batch, user_list, previous_session_batch, previous_session_lengths, prevoius_session_counts = datahandler.get_next_train_batch()

    print("Epoch", epoch, "finished")
    print("|- Epoch loss:", epoch_loss)
    
    ##
    ##  TESTING
    ##
    print("Starting testing")
    tester = Tester(1000)
    datahandler.reset_user_batch_data()
    _batch_number = 0
    xinput, targetvalues, sl, input_timestamps, input_timestamp_bucket_ids, session_reps, inter_session_seq_length, sess_rep_timestamps_batch, sess_rep_timestamp_bucket_ids_batch, user_list, previous_session_batch, previous_session_lengths, prevoius_session_counts = datahandler.get_next_test_batch()
    intra_rnn.eval()
    inter_rnn.eval()
    while len(xinput) > int(BATCH_SIZE / 2):
        batch_start_time = time.time()
        _batch_number += 1

        batch_predictions, sess_rep, inter_attn_weights, intra_attn_weights = predict(xinput, sl, session_reps, inter_session_seq_length, input_timestamps, input_timestamp_bucket_ids, sess_rep_timestamps_batch, sess_rep_timestamp_bucket_ids_batch, user_list, previous_session_batch, previous_session_lengths, prevoius_session_counts)

        # log intra attention weights
        if log_intra_attn and use_intra_attn and _batch_number % 3 == 0:
            for i in range(len(user_list)):
                if inter_session_seq_length[i] == 15 and sl[i] > 5:
                    datahandler.log_attention_weights_intra(intra_attn_weights, RUN_NAME, sl, batch_predictions, user_list[i], i)

        datahandler.store_user_session_representations(sess_rep, user_list, input_timestamps, input_timestamp_bucket_ids)

        # Evaluate predictions
        tester.evaluate_batch(batch_predictions, targetvalues, sl, user_list)

        # Print some stats during testing
        if _batch_number % 100 == 0:
            batch_runtime = time.time() - batch_start_time
            print("Batch number:", str(_batch_number), "/", str(num_test_batches), "\t Batch time:", "%.4f" % batch_runtime, "minutes", end='')
            eta = (batch_runtime*(num_test_batches-_batch_number)) / 60
            eta = "%.2f" % eta
            print("\t ETA:", eta, "minutes.")
        
        xinput, targetvalues, sl, input_timestamps, input_timestamp_bucket_ids, session_reps, inter_session_seq_length, sess_rep_timestamps_batch, sess_rep_timestamp_bucket_ids_batch, user_list, previous_session_batch, previous_session_lengths, prevoius_session_counts = datahandler.get_next_test_batch()

    # Print final test stats for epoch
    test_stats, current_recall5, current_recall20 = tester.get_stats_and_reset()
    print("Recall@5 = " + str(current_recall5))
    print("Recall@20 = " + str(current_recall20))
    print(test_stats)
    if epoch == 1:
        datahandler.log_config(message)
    datahandler.log_test_stats(epoch, epoch_loss, test_stats)
    tensorboard.scalar_summary('recall@5', current_recall5, epoch)
    tensorboard.scalar_summary('recall@20', current_recall20, epoch)
    tensorboard.scalar_summary('epoch_loss', epoch_loss, epoch)

    epoch += 1

    if save_model_parameters:
        torch.save(embed.state_dict(), HOME + "/savestates/" + RUN_NAME + "-embed_model.pth")
        torch.save(inter_rnn.state_dict(), HOME + "/savestates/" + RUN_NAME + "-inter_model.pth")
        torch.save(intra_rnn.state_dict(), HOME + "/savestates/" + RUN_NAME + "-intra_model.pth")
        torch.save(embed_optimizer.state_dict(), HOME + "/savestates/" + RUN_NAME + "-embed_optimizer.pth")
        torch.save(inter_optimizer.state_dict(), HOME + "/savestates/" + RUN_NAME + "-inter_optimizer.pth")
        torch.save(intra_optimizer.state_dict(), HOME + "/savestates/" + RUN_NAME + "-intra_optimizer.pth")