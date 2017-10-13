import tensorflow as tf
from tensorflow.contrib import layers
from tensorflow.contrib import rnn  # will probably be moved to code in TF 1.1. Keep it imported as rnn to make the rest of the code independent of this.
import datetime
import os
import time
import math
import numpy as np
from lastfm_utils_ii_rnn import IIRNNDataHandler
from test_util import Tester

reddit = "subreddit"
lastfm = "lastfm"
instacart = "instacart"

use_last_hidden_state = True

dataset = lastfm

do_training = True
save_best = True

home = os.path.expanduser('~')
dataset_path = home + '/datasets/'+dataset+'/4_train_test_split.pickle'
epoch_file = './epoch_file-iirnn-'+dataset+'.pickle'
checkpoint_file = './checkpoints/ii-rnn-'+dataset+'-'
checkpoint_file_ending = '.ckpt'
date_now = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d')
log_file = './testlog/'+str(date_now)+'-testing-ii-rnn.txt'

seed = 0
tf.set_random_seed(seed)

N_ITEMS      = -1
BATCHSIZE    = 2 # 100

if dataset == reddit:
    ST_INTERNALSIZE = 50
    LT_INTERNALSIZE = ST_INTERNALSIZE
    learning_rate = 0.001
    dropout_pkeep = 1.0
    MAX_SESSION_REPRESENTATIONS = 15
    MAX_EPOCHS = 31
elif dataset == lastfm:
    ST_INTERNALSIZE = 100
    LT_INTERNALSIZE = ST_INTERNALSIZE
    learning_rate = 0.001
    dropout_pkeep = 0.8
    MAX_SESSION_REPRESENTATIONS = 15
    MAX_EPOCHS = 10 # 50
elif dataset == instacart:
    ST_INTERNALSIZE = 80
    LT_INTERNALSIZE = ST_INTERNALSIZE
    learning_rate = 0.001
    dropout_pkeep = 0.8
    MAX_SESSION_REPRESENTATIONS = 15
    MAX_EPOCHS = 200

N_LAYERS     = 1        # number of layers in the rnn
SEQLEN       = 20-1     # maximum number of actions in a session (or more precisely, how far into the future an action affects future actions. This is important for training, but when running, we can have as long sequences as we want! Just need to keep the hidden state and compute the next action)
EMBEDDING_SIZE = ST_INTERNALSIZE
TOP_K = 20

# Load training data
datahandler = IIRNNDataHandler(dataset_path, BATCHSIZE, log_file, 
        MAX_SESSION_REPRESENTATIONS, LT_INTERNALSIZE)
N_ITEMS = datahandler.get_num_items()
N_SESSIONS = datahandler.get_num_training_sessions()

message = "------------------------------------------------------------------------\n"
if use_last_hidden_state:
    message += dataset + "with last hidden state\n"
else:
    message += dataset + "with average of embeddings\n"
message += "DATASET: "+dataset+" MODEL: II-RNN"
message += "\nCONFIG: N_ITEMS="+str(N_ITEMS)+" BATCHSIZE="+str(BATCHSIZE)
message += "\nST_INTERNALSIZE="+str(ST_INTERNALSIZE)+" LT_INTERNALSIZE="+str(LT_INTERNALSIZE)
message += "\nN_LAYERS="+str(N_LAYERS)+" SEQLEN="+str(SEQLEN)+" EMBEDDING_SIZE="+str(EMBEDDING_SIZE)
message += "\nN_SESSIONS="+str(N_SESSIONS)+" SEED="+str(seed)
message += "\nMAX_SESSION_REPRESENTATIONS="+str(MAX_SESSION_REPRESENTATIONS)
message += "\nDROPOUT="+str(dropout_pkeep)+" LEARNING_RATE="+str(learning_rate)
datahandler.log_config(message)
print(message)

if not do_training:
    print("\nOBS!!!! Training is turned off!\n")


##
## The model
##
print("Creating model")
cpu = ['/cpu:0']
gpu = ['/gpu:0', '/gpu:1']

# Use (CPU) RAM to hold embeddings. If >10 GB of VRAM available, you can put 
# this there instead, which should reduce runtime
with tf.device(cpu[0]):
    # Inputs
    X = tf.placeholder(tf.int32, [None, None], name='X')    # [ BATCHSIZE, SEQLEN ]
    Y_ = tf.placeholder(tf.int32, [None, None], name='Y_')  # [ BATCHSIZE, SEQLEN ]
    
    # Embeddings. W_embed = all embeddings. X_embed = retrieved embeddings 
    # from W_embed, corresponding to the items in the current batch
    W_embed = tf.Variable(tf.random_uniform([N_ITEMS, EMBEDDING_SIZE], -1.0, 1.0), name='embeddings')
    X_embed = tf.nn.embedding_lookup(W_embed, X) # [BATCHSIZE, SEQLEN, EMBEDDING_SIZE]

with tf.device(gpu[0]):
    # Length of sesssions (not considering padding)
    seq_len = tf.placeholder(tf.int32, [None], name='seqlen')
    batchsize = tf.placeholder(tf.int32, name='batchsize')
    
    # Average of embeddings session representation
    X_sum = tf.reduce_sum(X_embed, 1)
    X_avg = tf.transpose(tf.realdiv(tf.transpose(X_sum), tf.cast(seq_len, tf.float32)))

    lr = tf.placeholder(tf.float32, name='lr')              # learning rate
    pkeep = tf.placeholder(tf.float32, name='pkeep')        # dropout parameter

    # Input to inter-session RNN layer
    X_lt = tf.placeholder(tf.float32, [None, None, LT_INTERNALSIZE], name='X_lt') #[BATCHSIZE, (MAX SESSION LEN?,) LT_INTERNALSIZE]
    seq_len_lt = tf.placeholder(tf.int32, [None], name='lt_seqlen')

    # Inter-session RNN
    lt_cell = rnn.GRUCell(LT_INTERNALSIZE)
    lt_dropcell = rnn.DropoutWrapper(lt_cell, input_keep_prob=pkeep, output_keep_prob=pkeep)
    lt_rnn_outputs, lt_rnn_states = tf.nn.dynamic_rnn(lt_dropcell, X_lt,
            sequence_length=seq_len_lt, dtype=tf.float32)

    # Get the correct outputs (depends on session_lengths)
    last_lt_rnn_output = tf.gather_nd(lt_rnn_outputs, tf.stack([tf.range(batchsize), seq_len_lt-1], axis=1))

    # intra-session RNN
    onecell = rnn.GRUCell(ST_INTERNALSIZE)
    dropcell = rnn.DropoutWrapper(onecell, input_keep_prob=pkeep)
    multicell = rnn.MultiRNNCell([dropcell]*N_LAYERS, state_is_tuple=False)
    multicell = rnn.DropoutWrapper(multicell, output_keep_prob=pkeep)
    Yr, H = tf.nn.dynamic_rnn(multicell, X_embed, 
            sequence_length=seq_len, dtype=tf.float32, initial_state=last_lt_rnn_output)

    H = tf.identity(H, name='H') # just to give it a name

    # Apply softmax to the output
    # Flatten the RNN output first, to share weights across the unrolled time steps
    Yflat = tf.reshape(Yr, [-1, ST_INTERNALSIZE])         # [ BATCHSIZE x SEQLEN, ST_INTERNALSIZE ]
    # Change from internal size (from RNNCell) to N_ITEMS size
    Ylogits = layers.linear(Yflat, N_ITEMS)                     # [ BATCHSIZE x SEQLEN, N_ITEMS ]

#with tf.device(cpu[0]):
    # Flatten expected outputs to match actual outputs
    Y_flat_target = tf.reshape(Y_, [-1])    # [ BATCHSIZE x SEQLEN ]

    # Calculate loss
    loss = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=Ylogits, labels=Y_flat_target)    # [ BATCHSIZE x SEQLEN ]
    
    # Mask the losses (so we don't train in padded values)
    mask = tf.sign(tf.to_float(Y_flat_target))
    masked_loss = mask * loss

    # Unflatten loss
    loss = tf.reshape(masked_loss, [batchsize, -1])            # [ BATCHSIZE, SEQLEN ]

    # Get the index of the highest scoring prediction through Y
    Y = tf.argmax(Ylogits, 1)   # [ BATCHSIZE x SEQLEN ]
    Y = tf.reshape(Y, [batchsize, -1], name='Y')        # [ BATCHSIZE, SEQLEN ]
    
    # Get prediction
    top_k_values, top_k_predictions = tf.nn.top_k(Ylogits, k=TOP_K)        # [BATCHSIZE x SEQLEN, TOP_K]
    Y_prediction = tf.reshape(top_k_predictions, [batchsize, -1, TOP_K], name='YTopKPred')

    # Training
    train_step = tf.train.AdamOptimizer(lr).minimize(loss)

    # Stats
    # Average sequence loss
    seqloss = tf.reduce_mean(loss, 1)
    # Average batchloss
    batchloss = tf.reduce_mean(seqloss)

# Average number of correct predictions
accuracy = tf.reduce_mean(tf.cast(tf.equal(Y_, tf.cast(Y, tf.int32)), tf.float32))
loss_summary = tf.summary.scalar("batch_loss", batchloss)
acc_summary = tf.summary.scalar("batch_accuracy", accuracy)
summaries = tf.summary.merge([loss_summary, acc_summary])

# Init to save models
if not os.path.exists("checkpoints"):
    os.mkdir("checkpoints")
saver = tf.train.Saver(max_to_keep=1)



# Initialization
# istate = np.zeros([BATCHSIZE, ST_INTERNALSIZE*N_LAYERS])    # initial zero input state
init = tf.global_variables_initializer()
config = tf.ConfigProto(allow_soft_placement=True)
config.gpu_options.allow_growth = True      # be nice and don't use more memory than necessary
sess = tf.Session(config=config)
saver = tf.train.Saver()

##
##  TRAINING
##

print("Starting training.")

epoch = datahandler.get_latest_epoch(epoch_file)
print("|-Starting on epoch", epoch+1)
if epoch > 0:
    print("|--Restoring model.")
    save_file = checkpoint_file + checkpoint_file_ending
    saver.restore(sess, save_file)
else:
    sess.run(init)
epoch += 1

print()


best_recall5 = -1
best_recall20 = -1

num_training_batches = datahandler.get_num_training_batches()
num_test_batches = datahandler.get_num_test_batches()
while epoch <= MAX_EPOCHS:
    print("Starting epoch #"+str(epoch))
    epoch_loss = 0
    
    datahandler.reset_user_batch_data()
    datahandler.reset_user_session_representations()
    if do_training:
        _batch_number = 0
        xinput, targetvalues, sl, session_reps, sr_sl, user_list, _ = datahandler.get_next_train_batch()

        while len(xinput) > int(BATCHSIZE/2):
            _batch_number += 1
            batch_start_time = time.time()
    
            feed_dict = {X: xinput, Y_: targetvalues, X_lt: session_reps, 
                    seq_len_lt: sr_sl, lr: learning_rate, pkeep: dropout_pkeep, 
                    batchsize: len(xinput), seq_len: sl}
            if use_last_hidden_state:
                _, bl, sess_rep = sess.run([train_step, batchloss, H], feed_dict=feed_dict)
            else:
                _, bl, sess_rep = sess.run([train_step, batchloss, X_avg], feed_dict=feed_dict)

            print("SESSREP")
            print(type(sess_rep))
            
            datahandler.store_user_session_representations(sess_rep, user_list)
    
            batch_runtime = time.time() - batch_start_time
            epoch_loss += bl
            if _batch_number%100==0:
                print("Batch number:", str(_batch_number), "/", str(num_training_batches), "| Batch time:", "%.2f" % batch_runtime, " seconds", end='')
                print(" | Batch loss:", bl, end='')
                eta = (batch_runtime*(num_training_batches-_batch_number))/60
                eta = "%.2f" % eta
                print(" | ETA:", eta, "minutes.")
            
            xinput, targetvalues, sl, session_reps, sr_sl, user_list, _ = datahandler.get_next_train_batch()
    
        print("Epoch", epoch, "finished")
        print("|- Epoch loss:", epoch_loss)

    ##
    ##  TESTING
    ##
    print("Starting testing")
    tester = Tester()
    datahandler.reset_user_batch_data()
    _batch_number = 0
    xinput, targetvalues, sl, session_reps, sr_sl, user_list, _ = datahandler.get_next_test_batch()
    while len(xinput) > int(BATCHSIZE/2):
        batch_start_time = time.time()
        _batch_number += 1

        feed_dict = {X: xinput, pkeep: 1.0, batchsize: len(xinput), seq_len: sl,
                X_lt: session_reps, seq_len_lt: sr_sl}
     
        if use_last_hidden_state:
            batch_predictions, sess_rep = sess.run([Y_prediction, H], feed_dict=feed_dict)
        else:
            batch_predictions, sess_rep = sess.run([Y_prediction, X_avg], feed_dict=feed_dict)
            
        datahandler.store_user_session_representations(sess_rep, user_list)
        
        # Evaluate predictions
        tester.evaluate_batch(batch_predictions, targetvalues, sl)

        # Print some stats during testing
        batch_runtime = time.time() - batch_start_time
        if _batch_number%100==0:
            print("Batch number:", str(_batch_number), "/", str(num_test_batches), "| Batch time:", "%.2f" % batch_runtime, " seconds", end='')
            eta = (batch_runtime*(num_test_batches-_batch_number))/60
            eta = "%.2f" % eta
            print(" ETA:", eta, "minutes.")

        xinput, targetvalues, sl, session_reps, sr_sl, user_list, _ = datahandler.get_next_test_batch()

    # Print final test stats for epoch
    test_stats, current_recall5, current_recall20 = tester.get_stats_and_reset()
    print("Recall@5 = "+str(current_recall5))
    print("Recall@20 = "+str(current_recall20))
    
    if save_best:
        if current_recall5 > best_recall5:
            # Save the model
            print("Saving model.")
            save_file = checkpoint_file + checkpoint_file_ending
            save_path = saver.save(sess, save_file)
            print("|- Model saved in file:", save_path)

            best_recall5 = current_recall5

            datahandler.store_current_epoch(epoch, epoch_file)
            datahandler.log_test_stats(epoch, epoch_loss, test_stats)

    epoch += 1
