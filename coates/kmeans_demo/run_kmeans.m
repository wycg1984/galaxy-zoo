function centroids = run_kmeans(X, k, iterations)

% X is a matrix of shape (n_patches, n_pixels)
% k is the number of centroids


  % element-size square, then sum rows
  % x2 is matrix of shape (n_pathes, 1)
  x2 = sum(X.^2,2);

  % centroids = random matrix of shape (k, n_pixels)
  % second half looks to be commented out -- but otherwise would initialize centroids as randomly selected elements of X
  centroids = randn(k,size(X,2))*0.1;  %X(randsample(size(X,1), k), :);
  BATCH_SIZE=1000;
  
  
  for itr = 1:iterations
    fprintf('K-means iteration %d / %d\n', itr, iterations);

    % c2 = matrix of shape (k, 1)
    c2 = 0.5*sum(centroids.^2,2);

    % summation = matrix of shape (k, n_pixels)
    summation = zeros(k, size(X,2));
    counts = zeros(k, 1);
    
    loss =0;

    % step from 1 to n_patches in steps of BATCH_SIZE
    for i=1:BATCH_SIZE:size(X,1)
        % Index of of the last item in the batch
      lastIndex=min(i+BATCH_SIZE-1, size(X,1));
      $ m is the number of samples in this batch
      m = lastIndex - i + 1;


        % subset X for the rows in the batch, so shape is (batch_size, n_pixels) transposed
        % centroids is (k, n_pixels)
        % tmp is then shape of (k, batch_size)
      tmp = centroids*X(i:lastIndex,:)',

        % max gets the maximum in each column of the matrix
        % val is the max value, of shape (1, n_pixels)
        % labels is the index of the max value, of shape (1, n_pixels)
      [val,labels] = max(
      % subtracts c2 from each column of tmp
          bsxfun(@minus,
            tmp,
            c2
           )
          );
      loss = loss + sum(0.5*x2(i:lastIndex) - val');
      
      S = sparse(1:m,labels,1,m,k,m); % labels as indicator matrix
      summation = summation + S'*X(i:lastIndex,:);
      counts = counts + sum(S,1)';
    end


    centroids = bsxfun(@rdivide, summation, counts);
    
    % just zap empty centroids so they don't introduce NaNs everywhere.
    badIndex = find(counts == 0);
    centroids(badIndex, :) = 0;
  end