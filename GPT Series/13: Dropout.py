import tiktoken #used by openai for subword tokenization
import torch
import math
print(torch.cuda.is_available())
import torch.nn as nn
from torch.nn import functional as F

#------------------------------- HyperParameters ----------------------------#
device = 'cuda' if torch.cuda.is_available() else 'cpu'
batch_size = 64 # Batches
block_size = 256 # Time
num_iter = 10000  # number of iterations/epochs
eval_interval = 500 # how often to compute the loss
eval_iters = 200 # how many iterations to average for loss estimation
d_model = 512 # Channels: number of all features
d_k = 16 # number of features per head (should be smaller than d_model)
Nx = 6 # number of blocks/number of layers
dropout_rate = 0.2 # dropout rate
lr_rate = 1e-3 # learning rate in Adam optimizer
h = 6 # number of heads in Multi head attention


torch.manual_seed(1337)
#----------------------------- Loading Data ---------------------------------#
# check the input text, inspect the quality
with open('./GPT Series/input.txt', 'r', encoding = 'utf-8') as f:
    text = f.read()



#------------------ Characterization and Vocabulary -------------------------#
unique_chars = set(text)
list_unique_chars = list(unique_chars)
chars = sorted(list_unique_chars)
vocab_size = len(chars)



#------------------------ Tokenization 1: Character tokenization -------------------------#
# transforming characters to integers: mapping each character to integer
chars_to_int = {c:i for i, c in enumerate(chars)}
int_to_chars = {i:c for i, c in enumerate(chars)} #creating dictionary to show for each char the integer order int:char

# strings to integer
def encode(s):
    encoding = [chars_to_int[c] for c in s]
    return encoding
# list to strings
def decode(l):
    decoding = ''.join([int_to_chars[i] for i in l])
    return decoding
data = torch.tensor(encode(text), dtype=torch.long)



#------------------------------ Splitting Data Train/Valid -----------------------------#

# splitting the data into train and test
split_90perc = int(0.9*len(data))
#print("Size of the data: ", len(data))
#print("Training size: ", split_90perc)
#print("Validation or Test size: ", len(data) - split_90perc)
train_data = data[:split_90perc]
valid_data = data[split_90perc:]

torch.manual_seed(1337)

#-------------------------------- Creating Mini Batches ---------------------------------#
# function for creating mini batches
def get_batch(split):
    if split == "train":
        data = train_data
    else:
        data = valid_data
    ix = torch.randint(len(data)-block_size, (batch_size,))
    x = torch.stack([data[i:i+block_size] for i in ix]) # stack of sequences (batch size rows, block size columns) to use to predict Y
    y = torch.stack([data[i+1:i+block_size+1] for i in ix]) # stack of sequences from next token onwards in compared to x , what is to be predcited (real y)
    # adding this line to give the data to device in case cude:GPU
    x, y = x.to(device), y.to(device)
    return x,y



#----------------------------- Attention Mechanism -------------------------------------------------#
class SelfAttention(nn.Module):
    """Self Attention (One Head)"""
    """ d_k = C """
    def __init__(self, d_k):
        super().__init__() #superclass initialization for proper torch functionality
        d_k = d_model // h
        # keys 
        self.keys = nn.Linear(d_model, d_k, bias = False)
        # queries
        self.queries = nn.Linear(d_model, d_k, bias = False)
        # values
        self.values = nn.Linear(d_model, d_k, bias = False)
        # buffer for the model
        self.register_buffer('tril', torch.tril(torch.ones(block_size, block_size)))
        # dropout layer
        self.dropout = nn.Dropout(dropout_rate)

    def forward(self, X):
        """Computing Attention Matrix"""
        B, T, C = X.shape
        # Keys matrix K
        K = self.keys(X) # (B, T, C)
        # Query matrix Q
        Q = self.queries(X) # (B, T, C)
        # Scaled Dot Product
        scaled_dot_product = Q @ K.transpose(-2,-1) * 1/math.sqrt(C) # (B, T, T)
        # Masking upper triangle
        scaled_dot_product_masked = scaled_dot_product.masked_fill(self.tril[:T, :T]==0, float('-inf'))
        # SoftMax transformation
        attention_matrix = F.softmax(scaled_dot_product_masked, dim=-1) # (B, T, T)
        # Dropout
        attention_matrix = self.dropout(attention_matrix)
        # Weighted Aggregation
        V = self.values(X) # (B, T, C)
        output =  attention_matrix @ V # (B, T, C)
        return output
    

class MultiHeadAttention(nn.Module):
    """Multi Head Self Attention"""
    """h: #heads"""
    def __init__(self, h, d_k):
        super().__init__()
        # initializing the heads, we want h times attention heads wit size d_k
        self.heads = nn.ModuleList([SelfAttention(d_k) for _ in range(h)])
        # adding linear layer to project the concatenated heads to the original dimension
        self.projections = nn.Linear(h*d_k, d_model)
        # adding dropout layer
        self.droupout = nn.Dropout(dropout_rate)
    
    def forward(self, X):
        # running multiple self attention heads in parallel and concatinate them at channel dimension
        combined_attentions = torch.cat([h(X) for h in self.heads], dim = -1)
        # projecting the concatenated heads to the original dimension
        combined_attentions = self.projections(combined_attentions)
        # applying dropout
        combined_attentions = self.droupout(combined_attentions)
        return combined_attentions
    

class FeedForward(nn.Module):
    """FeedForward Layer with ReLU activation function"""

    def __init__(self, d_model):
        super().__init__()
        self.net = nn.Sequential(
            # 2 linear layers with ReLU activation function
            nn.Linear(d_model, 4*d_model),
            nn.ReLU(),
            nn.Linear(4*d_model, d_model),
            nn.Dropout(dropout_rate)
        )
    def forward(self, X):
        # applying the feedforward layer
        
        return self.net(X)
    

#---------------------------------- Layer Normalization ----------------------------------#
class LayerNorm:
    def __init__(self, dim, eps=1e-5):
        self.eps = eps
        self.gamma = torch.ones(dim)
        self.beta = torch.zeros(dim)

    def __call__(self, x):
        # orward pass calculaton
        xmean = x.mean(1, keepdim=True)  # layer mean
        xvar = x.var(1, keepdim=True)  # layer variance
        xhat = (x - xmean) / torch.sqrt(xvar + self.eps)  # normalize to unit variance
        self.out = self.gamma * xhat + self.beta      
        return self.out
    
    def parameters(self):
        return [self.gamma, self.beta]


# ---------------------------------- Blocks ----------------------------------#
class Block(nn.Module):
    """Multiple Blocks of Transformer"""
    def __init__(self, d_model, h):
        super().__init__()
        d_k = d_model // h
        # Layer 4: Adding Attention layer
        self.attention_head = MultiHeadAttention(h, d_k) # h heads of d_k dimensional self-attention
        # Layer 5: Feed Forward layer
        self.feedforward = FeedForward(d_model)
        # Layer Normalization 1
        self.ln1 = nn.LayerNorm(d_model)
        # Layer Normalization 2
        self.ln2 = nn.LayerNorm(d_model)
    
    # Adding additional X for Residual Connections
    def forward(self,X):
        X = X + self.attention_head(self.ln1(X))
        X = X + self.feedforward(self.ln2(X))
        return X

# ---------------------------------- Bigram Model ----------------------------------#
class BigramLM(nn.Module):

    def __init__(self):
        super().__init__()
        # Layer 1: Embedding Layer (embedding is of size vocab by d_model)
        self.token_embedding_table = nn.Embedding(vocab_size, d_model)
        # Layer 2: Adding Position Encoding Layer
        self.positional_encodings_table = nn.Embedding(block_size, d_model)
        # Repeating Multi Head Self Attention in sequential manner (Nx = 6 Blocks )
        self.blocks = nn.Sequential(
            Block(d_model, h = 4),
            Block(d_model, h = 4),
            Block(d_model, h = 4),
            #adding one to the blocks
            nn.LayerNorm(d_model),
        ) 
        # Adding Final Linear layer 
        self.lin_layer = nn.Linear(d_model, vocab_size) 


    def forward(self, idx, targets=None):    
        B, T = idx.shape
        # embedded space
        tok_embeddings = self.token_embedding_table(idx) # (B,T,C)
        # positional encoding
        pos_encodings = self.positional_encodings_table(torch.arange(T, device=device)) # here the torch.arange(T, device=device) is enumeration of integers from zero to T-1 to have the positions per token for all time steps 
        # adding embeddings to pos_encodings
        X = tok_embeddings + pos_encodings # (B, T, C) 
  
        # getting the logits
        logits = self.lin_layer(X) # (B, T, vocab_size) 

        if targets is None:
            loss = None
        else:
            B, T, C = logits.shape
            logits = logits.view(B*T, C )
            targets = targets.view(B*T)
            loss = F.cross_entropy(logits, targets)
        return logits, loss
    
    # function that generates updated tokens as the new tokens are added per time step, per batch
    def generate(self, idx, max_new_tokens):
        # idx is (B, T) array of indices in the current context 
        for _ in range(max_new_tokens):
            # cropping the idx to the size until last block_size tokens
            idx_cond = idx[:, -block_size:] # otherwise we will run out of index/scope in embeddings
            # predictions
            logits, loss = self(idx_cond)
            # limiting to last time step for bigram
            logits = logits[:, -1, :] # becomes (B, C)
            # softmax trasnformation 
            probs = F.softmax(logits, dim=-1) # (B, C)
            # sampling from generated probabilities for model novelty
            idx_next = torch.multinomial(probs, num_samples=1) # (B, 1) only single next value selected
            # appending the sampled index to the sequence
            idx = torch.cat((idx, idx_next), dim=1) # (B, T+1)
        return idx



#-------------------------------------- Estimating Loss ---------------------------------------#
# less noisy loss: average loss across every X batches
# making sure PyTorch doesn't store the parameters backwards
@torch.no_grad()
def estimate_loss():
    result = {}
    # setting model in evaluation state
    model.eval()
    for split in ['train', 'valid_date']:
        losses = torch.zeros(eval_iters)
        for e in range(eval_iters):
            X,Y = get_batch(split)
            logits, loss = model(X,Y)
            # storing each iterations loss
            losses[e] = loss.item()
        result[split] = losses.mean()
    # setting back to training state
    model.train()
    return result

model = BigramLM()
# moving model paramters to device
model_device = model.to(device)

#----------------------------- Building PyTorch optimizer using Adam -------------------------------#
optimizer = torch.optim.AdamW(model.parameters(), lr = lr_rate)
for iter in range(num_iter):
    # estimating the loss for per X interval
    if iter % eval_interval == 0:
       losses = estimate_loss()
       print(f"step {iter}: train loss is {losses['train']:.5f} and validation loss is {losses['valid_date']:.5f}")
    
    # sampling a mini batch of data
    xb, yb = get_batch("train")

    # Forward Pass 
    logits, loss = model(xb, yb)
    # Zeroing Gradients: Before computing the gradients, existing gradients are reset to zero. This is necessary because gradients accumulate by default in PyTorch.
    optimizer.zero_grad(set_to_none=True)
    # Backward Pass or Backpropogation: Computing Gradients
    loss.backward()
    # Updating the Model Parameters
    optimizer.step()
    #printing the Loss

context = torch.zeros((1, 1), dtype=torch.long, device = device)
print(decode(model.generate(context, max_new_tokens=500)[0].tolist()))

    
