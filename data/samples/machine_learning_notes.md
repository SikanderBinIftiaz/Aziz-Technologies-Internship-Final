# Machine Learning Study Notes

Study notes used as sample data for DocuMind AI.

## Supervised and Unsupervised Learning

Supervised learning trains a model on labeled examples to predict a target, for example classifying emails as spam or not spam. Unsupervised learning finds structure in unlabeled data, for example grouping customers with k-means clustering.

## Data Splits

A common split is 70 percent for training, 15 percent for validation and 15 percent for testing. The validation set is used to tune hyperparameters. The test set is used only once, at the end, to estimate real-world performance.

## Overfitting

Overfitting happens when a model memorizes the training data and performs poorly on new data. Signs include very high training accuracy but much lower validation accuracy. Remedies are collecting more data, simplifying the model, early stopping and regularization.

## Regularization

L1 regularization adds the absolute value of the weights to the loss and tends to produce sparse models. L2 regularization adds the squared weights and keeps all weights small. Dropout randomly disables neurons during training.

## Evaluation Metrics

Precision is the fraction of predicted positives that are correct. Recall is the fraction of actual positives that were found. The F1 score is the harmonic mean of precision and recall, calculated as 2 times precision times recall divided by the sum of precision and recall.

## Cross-Validation

In k-fold cross-validation the data is split into k parts. The model is trained k times, each time using a different part for validation. A typical choice is k equal to 5.

## Gradient Descent

Gradient descent minimizes the loss by repeatedly moving the weights in the direction opposite to the gradient. The learning rate controls the step size. A learning rate that is too high can cause divergence, and one that is too low makes training very slow.

## Retrieval-Augmented Generation

Retrieval-Augmented Generation, or RAG, combines a retriever with a language model. Documents are split into chunks, converted into embeddings and stored in a vector database. At question time, the most similar chunks are retrieved and given to the language model as context. This reduces hallucination and lets the model answer from private data. A chunk size of 500 to 1000 characters with a small overlap works well for most documents.
